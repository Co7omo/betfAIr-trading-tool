# Execution Engine Baseline — Design

**Data:** 2026-05-13
**Scope:** Costruire l'Execution Engine che riceve `decisions` con outcome=ALLOW, calcola size via Kelly fractional, piazza ordini LIMIT BACK con `customer_order_ref` idempotente, e persiste lifecycle events in `orders` + match deltas in `fills`. Include un Reconciler che polla gli ordini aperti come task background nello Scheduler. Due modalità: `dry_run` (no API call) e `paper` (FakeBetfairOrderClient). **Live esplicitamente NON in scope.**
**Out of scope:** Live trading, settlement reconciliation (P&L Engine), bankroll snapshots, order cancellation API, retry policy automatica, multi-market parallelism.

## 1. Obiettivo

Phase 2 ha messo in piedi il Decision Engine che produce `decisions` con outcome `ALLOW`/`BLOCK_SOFT`/`BLOCK_HARD`. Phase 3 piece 1 chiude il flusso: convertire un `ALLOW` in un ordine effettivo (paper o dry-run) con audit completo del lifecycle.

Vincoli mantenuti:
- **Append-only**: `orders` e `fills` solo INSERT. Stato corrente di un ordine = ultima riga ordinata DESC per `event_ts`.
- **Idempotency**: `customer_order_ref = decision_id.hex` (32 char hex deterministico). Stesso decision → stesso ref → Betfair rifiuta duplicate placement.
- **Audit chain**: `decision → orders → fills` joinable via `decision_id` + `customer_order_ref`.
- **Restart recovery**: a boot, il Reconciler riprende automaticamente gli ordini in `status IN ('PENDING', 'EXECUTABLE')` senza stato esterno.
- **Safe-stop**: il kill_switch gate del Decision Engine blocca nuove ALLOW. Il Reconciler continua a operare su ordini esistenti (allineato con SOP `09-failure-modes-runbook.md` §A).
- **No-live guard**: `ExecutionMode` ha solo `DRY_RUN` e `PAPER`. Aggiungere `LIVE` richiederebbe modifica esplicita dell'enum + opt-in al boot.

## 2. Architettura

### 2.1 Decomposizione

```
src/betfair_trading/
├── models/
│   └── order.py                            # NUOVO - OrderSide, OrderStatus, OrderEventType,
│                                           #   ExecutionMode, TradeIntent, OrderEvent, Fill
├── services/
│   ├── sizer.py                            # NUOVO - pure Kelly math
│   ├── execution_engine.py                 # NUOVO - orchestrator (on_decision_allow)
│   ├── reconciler.py                       # NUOVO - background polling task
│   ├── scheduler.py                        # + reconciler param + _reconcile_loop
│   └── decision_engine.py                  # evaluate() ritorna Decision | None
├── betfair_client/
│   └── client.py                           # + place_orders + list_current_orders
├── db/
│   └── writer.py                           # + insert_order_event, insert_fill, fetch_open_orders
└── main.py                                  # ExecutionEngine + Reconciler wiring

alembic/versions/
└── 004_orders_fills.py                     # NUOVO - orders + fills tables

tests/integration/fakes/
└── fake_betfair_client.py                  # + place_orders, list_current_orders, queue_match_behavior

config/
└── trading.yaml                            # + execution_mode, min_stake, reconcile_interval
```

### 2.2 ExecutionMode

```python
class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"  # compute intent + log + INSERT orders, no API call
    PAPER = "paper"      # FakeBetfairOrderClient (deterministic matching)
    # LIVE intentionally absent — adding it requires explicit enum extension + boot opt-in
```

### 2.3 Sizer — pure Kelly math (`services/sizer.py`)

Pure functions, niente DB/async. Mirror del pattern di `edge.py` e `gates.py`.

```python
def kelly_fraction(p_model: float, odds: float) -> float:
    """f* = (p*o - 1)/(o - 1).
    Returns 0.0 if odds <= 1.0 or f* < 0 (no edge → no trade).
    """
    if odds <= 1.0:
        return 0.0
    f_star = (p_model * odds - 1.0) / (odds - 1.0)
    return max(0.0, f_star)


def compute_stake(
    bankroll: float,
    p_model: float,
    odds: float,
    kelly_multiplier: float = 0.25,
    max_stake_fraction: float = 0.02,
    min_stake: float = 2.0,
) -> Decimal | None:
    """Stake = bankroll * kelly_multiplier * kelly_fraction(p_model, odds),
    capped at bankroll * max_stake_fraction. Returns None if below min_stake.
    Rounded to 2 decimal places.
    """
```

Sanity check `bankroll=1000, p=0.55, o=2.0, k=0.25, cap=0.02, min=2`:
- `f* = 0.10` → raw_stake = 25.0 → capped at 20.0 → stake = `Decimal("20.00")`.

### 2.4 ProbabilityProvider / Decision input

`ExecutionEngine.on_decision_allow(decision: Decision)`:
- `decision.selected_runner_id` → quale runner backare
- `decision.p_model[selected_runner_id]` → probabilità del modello (input al sizer)
- `best_back_price` recuperato da `market_snapshots` (latest per `(market_id, runner_id)`) → odds (input al sizer)

Il prezzo dell'ordine = `best_back_price` corrente (LIMIT al book).

### 2.5 ExecutionEngine flow

```
async def on_decision_allow(decision):
    if decision.decision_outcome != ALLOW: return None
    
    async with pool.acquire() as conn:
        # 1. Get latest quote for selected runner
        snap = SELECT best_back_price FROM market_snapshots
               WHERE market_id=$1 AND runner_id=$2
               ORDER BY snapshot_ts DESC LIMIT 1
        if snap is None or snap.best_back_price is None: return None
        odds = float(snap.best_back_price)
        
        # 2. Get model prob for selected
        p_model = decision.p_model[decision.selected_runner_id]
        
        # 3. Compute stake
        stake = compute_stake(bankroll, p_model, odds, kelly, cap, min_stake)
        if stake is None: return None  # below min, skip
        
        # 4. Build TradeIntent
        intent = TradeIntent(
            decision_id=decision.decision_id,
            market_id=decision.market_id, event_id=decision.event_id,
            selection_id=decision.selected_runner_id,
            side=OrderSide.BACK,
            price=Decimal(str(odds)),
            size=stake,
            customer_order_ref=decision.decision_id.hex,
        )
        
        # 5. Mode dispatch
        if mode == DRY_RUN:
            event = OrderEvent(..., status=PENDING, event_type=PLACED,
                              api_response=None, mode=DRY_RUN)
        else:  # PAPER
            try:
                response = await bf_client.place_orders(intent...)
                event = OrderEvent(...status from response,
                                  event_type=PLACED, api_response=response, mode=PAPER)
            except Exception as e:
                event = OrderEvent(..., status=ERROR, event_type=ERROR,
                                  api_response={"error": str(e)}, mode=PAPER)
        
        # 6. Persist
        order_event_id = await insert_order_event(conn, event)
    
    return order_event_id
```

### 2.6 Reconciler flow

```
async def reconcile_open_orders():
    async with pool.acquire() as conn:
        # 1. Find open orders (current state per customer_order_ref filtered by status)
        open_orders = await fetch_open_orders(conn, mode=self._mode)
        if not open_orders: return 0
        
        # 2. Skip API call in DRY_RUN
        if mode == DRY_RUN: return len(open_orders)
        
        # 3. Fetch current state from Betfair (fake or real)
        refs = [o.customer_order_ref for o in open_orders]
        current = await bf_client.list_current_orders(refs)
        current_by_ref = {c["customer_order_ref"]: c for c in current}
        
        # 4. Diff and persist
        for open_order in open_orders:
            current_state = current_by_ref.get(open_order.customer_order_ref)
            if current_state is None: continue  # not found — skip
            
            new_matched = Decimal(str(current_state.get("size_matched", 0)))
            new_status = OrderStatus(current_state.get("order_status", open_order.status.value))
            
            matched_delta = new_matched - open_order.matched_size
            
            if matched_delta > 0:
                # Write Fill (delta)
                fill = Fill(
                    customer_order_ref=open_order.customer_order_ref,
                    decision_id=open_order.decision_id,
                    market_id=open_order.market_id,
                    selection_id=open_order.selection_id,
                    matched_size_delta=matched_delta,
                    average_price_matched=Decimal(str(current_state["average_price_matched"])),
                    cumulative_matched_size=new_matched,
                    remaining_size=Decimal(str(current_state.get("size_remaining", 0))),
                )
                await insert_fill(conn, fill)
            
            if matched_delta > 0 or new_status != open_order.status:
                # Write LIFECYCLE event
                event = OrderEvent(
                    customer_order_ref=open_order.customer_order_ref,
                    decision_id=open_order.decision_id, ...,
                    matched_size=new_matched,
                    average_price_matched=...,
                    status=new_status,
                    event_type=LIFECYCLE,
                    api_response=current_state,
                    mode=open_order.mode,
                )
                await insert_order_event(conn, event)
        
        return len(open_orders)
```

### 2.7 Scheduler integration

`Scheduler.__init__` accetta nuovi parametri opzionali `reconciler` e `reconcile_interval`. Quando `reconciler is not None`, viene avviato un quarto task `_reconcile_loop` parallelo a discovery/poll/keepalive.

`reconciler=None` (default) → nessun reconcile loop → Phase 2 comportamento invariato.

### 2.8 Refactor DecisionEngine.evaluate

Cambia signature da:
```python
async def evaluate(...) -> uuid.UUID | None
```
a:
```python
async def evaluate(...) -> Decision | None
```

Il `Decision` ritornato contiene `decision_id`, quindi i consumer che leggevano solo l'id passano da `decision_id = await evaluate(...)` a `decision = await evaluate(...); decision_id = decision.decision_id`.

Test esistenti in `test_pipeline_decision.py` vengono aggiornati. Nessun impatto comportamentale.

### 2.9 Wiring `main.py`

```python
from betfair_trading.models.order import ExecutionMode
from betfair_trading.services.execution_engine import ExecutionEngine
from betfair_trading.services.reconciler import Reconciler

execution_mode = ExecutionMode(trading.get("execution_mode", "dry_run"))
execution_engine = ExecutionEngine(
    pool=pool, bf_client=bf_client, mode=execution_mode,
    bankroll=trading.get("initial_bankroll", 1000.0),
    kelly_multiplier=trading.get("kelly_fraction", 0.25),
    max_stake_fraction=trading.get("max_stake_fraction", 0.02),
    min_stake=trading.get("min_stake", 2.0),
)
reconciler = Reconciler(pool=pool, bf_client=bf_client, mode=execution_mode)

async def on_snapshot_with_pipeline(bundle, snapshot_ids):
    fv_ids = await feature_builder.on_market_snapshot(bundle, snapshot_ids)
    if fv_ids:
        decision = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
        if decision is not None and decision.decision_outcome == DecisionOutcome.ALLOW:
            await execution_engine.on_decision_allow(decision)

scheduler = Scheduler(
    collector, raw_client,
    poll_interval=..., discovery_interval=...,
    reconciler=reconciler,
    reconcile_interval=trading.get("reconcile_interval", 10),
)
scheduler.set_snapshot_callback(on_snapshot_with_pipeline)
```

## 3. Schema `orders` + `fills`

Migrazione `alembic/versions/004_orders_fills.py`:

```sql
CREATE TABLE orders (
    order_event_id           UUID         NOT NULL DEFAULT uuid_generate_v4(),
    customer_order_ref       TEXT         NOT NULL,
    decision_id              UUID         NOT NULL,
    market_id                TEXT         NOT NULL,
    event_id                 TEXT         NOT NULL,
    selection_id             BIGINT       NOT NULL,
    side                     TEXT         NOT NULL,
    requested_price          NUMERIC(10,4) NOT NULL,
    requested_size           NUMERIC(14,2) NOT NULL,
    matched_size             NUMERIC(14,2) NOT NULL DEFAULT 0,
    average_price_matched    NUMERIC(10,4),
    status                   TEXT         NOT NULL,
    event_type               TEXT         NOT NULL,
    event_ts                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    api_response             JSONB,
    mode                     TEXT         NOT NULL,
    PRIMARY KEY (order_event_id),
    CONSTRAINT orders_side_check CHECK (side IN ('BACK', 'LAY')),
    CONSTRAINT orders_event_type_check
        CHECK (event_type IN ('PLACED', 'LIFECYCLE', 'CANCELLED', 'ERROR')),
    CONSTRAINT orders_mode_check CHECK (mode IN ('dry_run', 'paper'))
);
CREATE INDEX idx_orders_customer_ref ON orders (customer_order_ref, event_ts DESC);
CREATE INDEX idx_orders_decision ON orders (decision_id);
CREATE INDEX idx_orders_market ON orders (market_id, event_ts);

CREATE TABLE fills (
    fill_id                  UUID         NOT NULL DEFAULT uuid_generate_v4(),
    customer_order_ref       TEXT         NOT NULL,
    decision_id              UUID         NOT NULL,
    market_id                TEXT         NOT NULL,
    selection_id             BIGINT       NOT NULL,
    fill_ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    matched_size_delta       NUMERIC(14,2) NOT NULL,
    average_price_matched    NUMERIC(10,4) NOT NULL,
    cumulative_matched_size  NUMERIC(14,2) NOT NULL,
    remaining_size           NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (fill_id)
);
CREATE INDEX idx_fills_customer_ref ON fills (customer_order_ref, fill_ts);
CREATE INDEX idx_fills_market ON fills (market_id, fill_ts);
```

Update `tests/integration/conftest.py`: aggiungere `orders, fills` al TRUNCATE.
Update `tests/integration/test_pg_smoke.py`: aggiungere `"orders", "fills"` al set `expected`.

## 4. Pydantic contracts — `models/order.py`

```python
class OrderSide(StrEnum):
    BACK = "BACK"
    LAY = "LAY"

class OrderStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTABLE = "EXECUTABLE"
    EXECUTION_COMPLETE = "EXECUTION_COMPLETE"
    CANCELLED = "CANCELLED"
    LAPSED = "LAPSED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"

class OrderEventType(StrEnum):
    PLACED = "PLACED"
    LIFECYCLE = "LIFECYCLE"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"

class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"


class TradeIntent(BaseModel):
    decision_id: UUID
    market_id: str
    event_id: str
    selection_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    customer_order_ref: str


class OrderEvent(BaseModel):
    order_event_id: UUID = Field(default_factory=uuid4)
    customer_order_ref: str
    decision_id: UUID
    market_id: str
    event_id: str
    selection_id: int
    side: OrderSide
    requested_price: Decimal
    requested_size: Decimal
    matched_size: Decimal = Decimal("0")
    average_price_matched: Decimal | None = None
    status: OrderStatus
    event_type: OrderEventType
    event_ts: datetime | None = None
    api_response: dict | None = None
    mode: ExecutionMode


class Fill(BaseModel):
    fill_id: UUID = Field(default_factory=uuid4)
    customer_order_ref: str
    decision_id: UUID
    market_id: str
    selection_id: int
    fill_ts: datetime | None = None
    matched_size_delta: Decimal
    average_price_matched: Decimal
    cumulative_matched_size: Decimal
    remaining_size: Decimal
```

## 5. Estensione `AsyncBetfairClient`

In `betfair_client/client.py`, due nuovi metodi:

```python
async def place_orders(
    self, market_id: str, customer_order_ref: str,
    selection_id: int, side: str,
    price: Decimal, size: Decimal,
    persistence_type: str = "LAPSE",
) -> dict:
    """Single LIMIT order placement. Returns summarized instruction report."""

async def list_current_orders(
    self, customer_order_refs: list[str],
) -> list[dict]:
    """Fetch current state for the given refs."""
```

L'implementazione **reale** verso `betfairlightweight.betting.place_orders` viene aggiunta ma NON wirata in main.py per Phase 3 baseline (mode=`paper` usa solo il fake).

## 6. Estensione `FakeAsyncBetfairClient`

```python
class FakeAsyncBetfairClient:
    def __init__(self):
        # ...existing...
        self._placed_orders: dict[str, dict] = {}
        self._matching_behavior: dict[str, str] = {}

    def queue_match_behavior(self, customer_order_ref: str, behavior: str):
        """'instant_match' | 'partial' | 'no_match' | 'lapse'"""
        self._matching_behavior[customer_order_ref] = behavior

    async def place_orders(...) -> dict:
        """Records order, returns synthetic instruction report based on _matching_behavior."""

    async def list_current_orders(self, customer_order_refs) -> list[dict]:
        """Returns synthetic state for placed orders."""
```

## 7. DB writer additions

In `db/writer.py`:
- `insert_order_event(conn, event: OrderEvent) -> UUID`
- `insert_fill(conn, fill: Fill) -> UUID`
- `fetch_open_orders(conn, mode: ExecutionMode) -> list[OrderEvent]` (helper read: uses DISTINCT ON + status filter)

## 8. Test strategy

### 8.1 Unit tests

**`tests/unit/test_sizer.py`** (6 test):
1. `test_kelly_fraction_positive_edge` — `p=0.55, o=2.0 → 0.10`
2. `test_kelly_fraction_zero_when_negative_edge` — `p=0.40, o=2.0 → 0.0`
3. `test_kelly_fraction_zero_when_odds_le_one` — `o=0.5 → 0.0`
4. `test_compute_stake_capped_at_max_fraction` — bankroll=1000, capped at 20.0
5. `test_compute_stake_below_min_returns_none` — bankroll basso → None
6. `test_compute_stake_zero_kelly_returns_none` — negative edge → None

**`tests/unit/test_order_model.py`** (3 test):
1. `test_trade_intent_construction`
2. `test_order_event_with_default_event_ts`
3. `test_fill_construction`

### 8.2 Integration tests

**`tests/integration/test_order_writers.py`** (3 test):
1. `test_insert_order_event_persists`
2. `test_insert_fill_persists`
3. `test_fetch_open_orders_filters_by_status_and_mode`

**`tests/integration/test_execution_engine.py`** (5 test):
1. `test_dry_run_writes_placed_event_no_api_call`
2. `test_paper_mode_calls_fake_client_writes_lifecycle`
3. `test_sizing_below_min_stake_skips_order`
4. `test_customer_order_ref_is_decision_id_hex`
5. `test_block_outcome_does_not_trigger_execution`

**`tests/integration/test_reconciler.py`** (5 test):
1. `test_reconcile_no_open_orders_returns_zero`
2. `test_reconcile_open_order_with_match_writes_fill_and_lifecycle`
3. `test_reconcile_partial_match_writes_delta`
4. `test_reconcile_no_change_writes_nothing`
5. `test_terminal_state_not_picked_up_for_reconcile`

**`tests/integration/test_pipeline_execution.py`** (2 test end-to-end):
1. `test_full_pipeline_allow_to_placed_order` — collector + fb + de + ee + reconciler: ALLOW → order → fill
2. `test_dry_run_pipeline_writes_pending_no_fills`

### 8.3 Modifiche ai test esistenti

- `tests/integration/test_pipeline_decision.py`: aggiornare le asserzioni `decision_id = await evaluate(...)` → `decision = await evaluate(...); decision.decision_id`.
- `tests/integration/conftest.py`: TRUNCATE include `orders, fills`.
- `tests/integration/test_pg_smoke.py`: `expected` include `"orders", "fills"`.

## 9. Modifiche al codice di produzione (riassunto)

| File | Cambiamento |
|---|---|
| `alembic/versions/004_orders_fills.py` | NUOVO |
| `src/betfair_trading/models/order.py` | NUOVO |
| `src/betfair_trading/db/writer.py` | + 3 functions |
| `src/betfair_trading/services/sizer.py` | NUOVO (pure) |
| `src/betfair_trading/services/execution_engine.py` | NUOVO |
| `src/betfair_trading/services/reconciler.py` | NUOVO |
| `src/betfair_trading/services/scheduler.py` | + reconciler param + _reconcile_loop |
| `src/betfair_trading/services/decision_engine.py` | evaluate() ritorna `Decision \| None` |
| `src/betfair_trading/betfair_client/client.py` | + 2 methods |
| `src/betfair_trading/main.py` | Wiring ExecutionEngine + Reconciler + pipeline callback |
| `tests/integration/fakes/fake_betfair_client.py` | + 2 methods + builder |
| `config/trading.yaml` | + execution_mode, min_stake, reconcile_interval |

## 10. Vincoli di correttezza

- **Append-only**: `orders` e `fills` pure-INSERT. Query "current state" via `SELECT DISTINCT ON (customer_order_ref) ... ORDER BY ... DESC`.
- **Idempotency**: `customer_order_ref = decision.decision_id.hex` (32 chars). Stesso decision → stesso ref. Betfair (e fake) rifiutano duplicate.
- **Audit chain**: `decision → orders → fills` joinable via `decision_id` + `customer_order_ref`. Test esplicito `test_full_pipeline_allow_to_placed_order`.
- **Restart recovery**: `fetch_open_orders(mode)` ricostruisce automaticamente lo stato post-restart. Nessuna lock o state esterno.
- **No-live guard**: `ExecutionMode` enum manca esplicitamente di `LIVE`. Test deterministico verifica che `execution_mode: 'live'` in config raise alla costruzione di `ExecutionMode("live")` (Pydantic StrEnum validation).
- **Safe-stop coerente con kill_switch**: nuove decisioni ALLOW vengono filtrate dal gate `kill_switch` del Decision Engine → nessun nuovo `on_decision_allow`. Il Reconciler resta attivo sugli ordini esistenti (`status IN ('PENDING', 'EXECUTABLE')`).

## 11. Non in questo spec

- **Live trading**: aggiungere `ExecutionMode.LIVE` + wiring main.py. Iterazione futura con guard rigorosi.
- **Settlement reconciliation** (`listClearedOrders`) e P&L: P&L Engine, prossimo task Phase 3.
- **Order cancellation API** (`cancelOrders`): ordini si auto-lapse a inplay via `persistence_type=LAPSE`. Manual cancel è un follow-up.
- **Retry policy automatica** su HTTP errors: deferred. `customer_order_ref` rende sicuro il retry manuale.
- **Multi-market parallelism**: ExecutionEngine processa una decisione alla volta (sequenziale nel callback). Concorrenza viene dalla single-event-loop async, non da thread.
- **Backtesting harness** (replay decisioni storiche): follow-up indipendente.
- **Bankroll snapshot tracking**: lo `_bankroll` in ExecutionEngine è una costante dal config. Aggiornamento dinamico (post-settlement) viene con P&L Engine.

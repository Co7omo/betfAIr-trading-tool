# Decision Engine + Risk Gates — Design

**Data:** 2026-05-10
**Scope:** Costruire il Decision Engine che consuma `feature_vectors` (A0/A1/A2), calcola edge per ogni outcome 1X2, applica i risk gate, e persiste una decisione audit-completa nella nuova tabella `decisions`. Si interfaccia con il Model Inference tramite un Protocol `ProbabilityProvider` (con stub per Phase 2).
**Out of scope:** Model Inference reale, Execution Engine, P&L Engine, `bankroll_snapshots`, `model_inferences` (verranno con i rispettivi task).

## 1. Obiettivo

Phase 1 ha messo in piedi la pipeline di feature; il task A1/A2 ha cablato il Feature Builder con l'External Data. Adesso introduciamo il consumatore naturale: il Decision Engine. Per ogni snapshot in finestra, valuta i 3 outcome 1X2, sceglie il candidate con il maggior `edge_net`, applica i gate (kill switch, finestra, edge, liquidità, spread, position cap, daily DD), e persiste un audit row in `decisions` indipendentemente dall'outcome.

Vincoli mantenuti:
- **Audit-first**: ogni `evaluate()` produce un row, anche su BLOCK_*. Tutti i gate sono persistiti.
- **Append-only**: niente UPDATE; `decisions` ha solo INSERT.
- **Kill switch live**: query `config_snapshots` ad ogni evaluate (no cache stale).
- **Reproducibility**: ogni decision linka `feature_vector_ids[]`, `model_version`, `config_snapshot_id`.

## 2. Architettura

### 2.1 Decomposizione

3 file nuovi sotto `src/betfair_trading/services/`:

- `edge.py` — pure functions: `compute_market_probs`, `compute_net_edge`. Niente DB, niente async.
- `gates.py` — pure predicates: 7 gate, ognuno ritorna `tuple[bool, str]`. Niente DB.
- `decision_engine.py` — classe `DecisionEngine` (orchestrator). Async, fa il read-acquire-write con asyncpg.

Più 1 file Protocol+stub:

- `probability_providers.py` — Protocol `ProbabilityProvider` + 2 stub: `MarketImpliedProvider`, `BiasedStubProvider`.

Plus persistence:

- `models/decision.py` — Pydantic `Decision` + `GateResult` + `DecisionOutcome` enum.
- `db/writer.py` — funzione `insert_decision`.
- `alembic/versions/002_decisions.py` — schema migration.

### 2.2 ProbabilityProvider Protocol

```python
class ProbabilityProvider(Protocol):
    """Returns model probability per runner. Sum across 3 runners == 1.0."""

    @property
    def model_version(self) -> str: ...

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]: ...
```

Stub implementations:

- **`MarketImpliedProvider`** — calcola le market-implied probs dal bundle e le restituisce. `model_version = "STUB_MARKET_IMPLIED_V1"`. Usato per sanity test: edge_gross sempre 0.
- **`BiasedStubProvider(home_bias=0.05)`** — calcola le market-implied, applica un bias deterministico (+0.05 al home, -0.025 redistribuiti su draw e away). `model_version = "STUB_BIAS_V1"`. Usato per test in cui ci aspettiamo ALLOW sul home.

Nessuno dei 2 usa `feature_vector_ids` (gli stub non hanno modello). Il vero `ModelInferenceProvider` (Phase 3) li userà.

### 2.3 Edge math (`edge.py`)

```python
def compute_market_probs(runner_quotes: dict[int, float | None]) -> dict[int, float]:
    """Normalize 1/odds_i across runners with valid quotes.
    Returns {runner_id: prob}; sum == 1.0 for runners with non-None positive odds.
    Runners with None or non-positive prices get prob=0.0.
    """

def compute_net_edge(
    p_model: float, p_market: float, commission_rate: float = 0.05,
) -> tuple[float, float]:
    """Returns (edge_gross, edge_net).
    edge_gross = p_model - p_market
    edge_net = p_model * (1 - commission_rate) - p_market
             = edge_gross - commission_rate * p_model
    Slippage is 0 in Phase 2 (configurable later via additional argument).
    """
```

Sanity check con `p_model=0.55, p_market=0.50, commission=0.05`:
- edge_gross = 0.05
- edge_net = 0.55 × 0.95 − 0.50 = 0.0225 (≈ 2.25%, sopra default 2% threshold → trigger)

### 2.4 Gate predicates (`gates.py`)

Ognuno ritorna `tuple[bool, str]` (`passed`, `reason`).

```python
def check_kill_switch(active: bool) -> tuple[bool, str]
def check_window(minutes_to_start, window_start_min, window_end_min)
    """minutes_to_start ∈ [window_end_min, window_start_min]"""
def check_edge_threshold(edge_net, threshold)
def check_liquidity(best_back_size, min_liquidity)
def check_spread(spread, max_spread)
def check_position_limit(allow_count_for_event, max_per_event)
def check_daily_drawdown(current_dd_fraction, max_dd_fraction)
```

`check_daily_drawdown` è uno **stub Phase 2**: il valore corrente è hardcoded 0.0 finché non esiste P&L. Il gate ritorna sempre PASS, con `reason="stub_until_pnl_engine"`. Verrà sostituito con un calcolo reale quando `pnl_events` esiste.

### 2.5 Decision flow — `DecisionEngine.evaluate(bundle, snapshot_ids, feature_vector_ids)`

```
1. async with self._pool.acquire() as conn:
2.   runners_meta = await self._load_runners(conn, bundle.market_id)  # cached, list[Runner]
3.   config_row = await conn.fetchrow(
        "SELECT config_snapshot_id, kill_switch_active "
        "FROM config_snapshots ORDER BY effective_ts DESC LIMIT 1")
4.   # Index bundle runners by runner_id for O(1) lookup
     bundle_by_id = {rs.runner_id: rs for rs in bundle.runners}
5.   runner_quotes = {
        r.runner_id: float(bundle_by_id[r.runner_id].best_back_price)
        if (r.runner_id in bundle_by_id and bundle_by_id[r.runner_id].best_back_price)
        else None
        for r in runners_meta
     }
6.   p_market = compute_market_probs(runner_quotes)
7.   p_model = await self._provider.get_probabilities(bundle, runners_meta, feature_vector_ids)
8.   edges = {r.runner_id: compute_net_edge(p_model[r.runner_id], p_market[r.runner_id],
                                             self._commission_rate)
              for r in runners_meta}
9.   selected_runner_id = max(edges, key=lambda rid: edges[rid][1])  # max edge_net
10.  selected_edge_net = edges[selected_runner_id][1]
11.  selected_runner_snapshot = bundle_by_id[selected_runner_id]
12.  allow_count = await conn.fetchval(
        "SELECT COUNT(*) FROM decisions "
        "WHERE event_id = $1 AND decision_outcome = 'ALLOW'",
        bundle.event_id)
13.  gate_results = {
        "kill_switch": check_kill_switch(config_row["kill_switch_active"]),
        "window": check_window(bundle.minutes_to_start,
                                self._window_start_minutes, self._window_end_minutes),
        "edge_threshold": check_edge_threshold(selected_edge_net, self._edge_threshold),
        "liquidity": check_liquidity(selected_runner_snapshot.best_back_size, self._min_liquidity),
        "spread": check_spread(selected_runner_snapshot.spread, self._max_spread),
        "position_limit": check_position_limit(allow_count, self._max_positions_per_event),
        "daily_drawdown": check_daily_drawdown(0.0, self._daily_dd_max),
      }
14.  outcome = self._determine_outcome(gate_results)  # ALLOW|BLOCK_SOFT|BLOCK_HARD
15.  rationale = self._build_rationale(gate_results, outcome)
16.  decision = Decision(market_id, event_id, ..., gate_results, outcome, rationale, ...)
17.  decision_id = await insert_decision(conn, decision)
18. return decision_id
```

Note: `check_window` uses `bundle.minutes_to_start` (already computed by the Collector) to avoid an extra DB lookup of `markets.start_time`.

`_determine_outcome` rule:
- if `kill_switch.passed == False` → BLOCK_HARD
- elif any other gate `.passed == False` → BLOCK_SOFT
- else → ALLOW

I gate sono valutati **tutti** anche se uno fallisce — il rationale è una stringa concatenata (es. `"edge_threshold:edge_below_threshold; liquidity:size_below_min"`).

### 2.6 Wiring nel main.py

```python
provider = BiasedStubProvider(home_bias=0.05)  # Phase 2 stub
decision_engine = DecisionEngine(
    pool=pool, provider=provider,
    edge_threshold=trading.get("edge_threshold", 0.02),
    min_liquidity=trading.get("min_liquidity", 100.0),
    max_spread=trading.get("max_spread", 0.10),
    commission_rate=0.05,
    max_positions_per_event=trading.get("max_positions_per_event", 1),
    window_start_minutes=trading.get("window_start_minutes", 120),
    window_end_minutes=trading.get("window_end_minutes", 10),
    daily_dd_max=trading.get("daily_stop_loss_fraction", 0.05),
)

async def on_snapshot_with_decision(bundle, snapshot_ids):
    fv_ids = await feature_builder.on_market_snapshot(bundle, snapshot_ids)
    if fv_ids:
        await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)

scheduler.set_snapshot_callback(on_snapshot_with_decision)
```

## 3. Schema `decisions`

Migrazione `alembic/versions/002_decisions.py`:

```sql
CREATE TABLE decisions (
    decision_id          UUID         NOT NULL DEFAULT uuid_generate_v4(),
    market_id            TEXT         NOT NULL,
    event_id             TEXT         NOT NULL,
    snapshot_id          UUID,
    decision_ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    model_version        TEXT         NOT NULL,
    p_model              JSONB        NOT NULL,
    p_market             JSONB        NOT NULL,
    edge_gross           JSONB        NOT NULL,
    edge_net             JSONB        NOT NULL,

    selected_runner_id   BIGINT,
    selected_edge_net    NUMERIC(10,6),

    gate_results         JSONB        NOT NULL,
    decision_outcome     TEXT         NOT NULL,
    rationale            TEXT,

    feature_vector_ids   UUID[]       NOT NULL,
    config_snapshot_id   UUID,

    PRIMARY KEY (decision_id),
    CONSTRAINT decisions_outcome_check
        CHECK (decision_outcome IN ('ALLOW', 'BLOCK_SOFT', 'BLOCK_HARD'))
);

CREATE INDEX idx_decisions_event ON decisions (event_id, decision_ts);
CREATE INDEX idx_decisions_market_outcome
    ON decisions (market_id, decision_outcome, decision_ts);
CREATE INDEX idx_decisions_event_allow
    ON decisions (event_id) WHERE decision_outcome = 'ALLOW';
```

L'ultimo indice partial accelera il gate `check_position_limit`.

Non creiamo una tabella `risk_evaluations` separata: i `gate_results` JSONB nel `decisions` row sono self-contained per Phase 2. Splitting in tabella separata è un follow-up se emergono use case analitici dedicati.

## 4. Pydantic contract — `models/decision.py`

```python
class DecisionOutcome(StrEnum):
    ALLOW = "ALLOW"
    BLOCK_SOFT = "BLOCK_SOFT"
    BLOCK_HARD = "BLOCK_HARD"

class GateResult(BaseModel):
    passed: bool
    reason: str

class Decision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    market_id: str
    event_id: str
    snapshot_id: UUID | None = None
    decision_ts: datetime
    model_version: str
    p_model: dict[int, float]
    p_market: dict[int, float]
    edge_gross: dict[int, float]
    edge_net: dict[int, float]
    selected_runner_id: int | None
    selected_edge_net: Decimal | None
    gate_results: dict[str, GateResult]
    decision_outcome: DecisionOutcome
    rationale: str | None
    feature_vector_ids: list[UUID]
    config_snapshot_id: UUID | None
```

## 5. Test strategy

### 5.1 Unit tests (no DB)

- **`tests/unit/test_edge.py`** (4 test): market_probs normalize, missing-quote handling, net_edge zero-commission, net_edge default-commission.
- **`tests/unit/test_gates.py`** (8-10 test): happy + failure path per ogni gate.
- **`tests/unit/test_probability_providers.py`** (3 test): MarketImpliedProvider normalization, BiasedStubProvider home-shift, providers handle missing quotes (degraded fallback).

### 5.2 Integration tests — `tests/integration/test_pipeline_decision.py` (7 test)

1. **`test_allow_path_with_biased_provider`** — BiasedStubProvider → home edge sopra threshold → 1 row decisions, ALLOW, selected_runner_id=home_runner.
2. **`test_block_soft_when_market_implied_provider`** — MarketImpliedProvider → edge=0 → BLOCK_SOFT, edge_threshold gate failed.
3. **`test_block_soft_low_liquidity`** — book con size=50 (< min 100) → BLOCK_SOFT, liquidity gate failed.
4. **`test_block_soft_high_spread`** — book con spread 0.50 (> max 0.10) → BLOCK_SOFT, spread gate failed.
5. **`test_block_hard_kill_switch`** — `INSERT INTO config_snapshots ... kill_switch_active=TRUE` → BLOCK_HARD anche con BiasedStubProvider.
6. **`test_position_limit_blocks_second_allow`** — primo evaluate → ALLOW; secondo evaluate stesso event → BLOCK_SOFT, position_limit failed.
7. **`test_decision_persists_full_audit`** — verifica payload: p_model, p_market, edge_gross, edge_net contengono 3 runner; gate_results contiene 7 gate; feature_vector_ids array popolato; config_snapshot_id non NULL.

### 5.3 Modifica callback test esistenti

Nessuna. I test in `test_pipeline_feature_builder.py` e `test_pipeline_a1_a2.py` non chiamano `decision_engine.evaluate()`. Continueranno a osservare solo feature_vectors. Quando l'integrazione end-to-end è cablata in main.py, i test esistenti potrebbero osservare anche decisions (a seconda di come il main wiring viene testato), ma per Phase 2 il scope è l'evaluate isolato.

## 6. Modifiche al codice di produzione

| File | Cambiamento |
|---|---|
| `alembic/versions/002_decisions.py` | NUOVO — schema migration |
| `src/betfair_trading/models/decision.py` | NUOVO — Pydantic contract |
| `src/betfair_trading/db/writer.py` | + `insert_decision` |
| `src/betfair_trading/services/edge.py` | NUOVO — pure edge math |
| `src/betfair_trading/services/gates.py` | NUOVO — pure gate predicates |
| `src/betfair_trading/services/probability_providers.py` | NUOVO — Protocol + 2 stubs |
| `src/betfair_trading/services/decision_engine.py` | NUOVO — orchestrator |
| `src/betfair_trading/main.py` | Wiring: provider + engine + wrapped callback |

Niente modifiche a `feature_builder.py` o `external_ingestor.py`.

## 7. Vincoli di correttezza

- **Audit-first**: ogni `evaluate()` produce 1 riga, anche su BLOCK_*. Tutti i gate vengono persistiti.
- **Append-only**: `decisions` solo INSERT. Truncate solo nei test via `clean_db` autouse fixture.
- **Kill switch live**: query `config_snapshots` su ogni `evaluate()`. Latency: 1 SELECT in più per decision (negligible).
- **Reproducibility**: ogni decision linka `feature_vector_ids[]`, `model_version`, `config_snapshot_id`.
- **Determinismo gate**: tutte le funzioni in `gates.py` sono pure (input → output, no side effects).

## 8. Non in questo spec

- **`model_inferences` table** — verrà con il vero Model Inference. Per ora le probabilità del provider sono persistite direttamente in `decisions.p_model` con `model_version` come traccia.
- **`bankroll_snapshots` table + daily DD reale** — Phase 3 con P&L Engine. Il gate `check_daily_drawdown` è uno stub PASS-through.
- **`customerOrderRef` generation + Execution wiring** — Phase 3.
- **Backtesting harness** consumer di `decisions` — follow-up indipendente.
- **Config reload runtime** — il kill switch è live via query, ma altri parametri (edge_threshold, ecc.) sono letti al boot. Live reload è un follow-up.
- **Risk evaluator separato (`risk_evaluations` table)** — JSONB embedded in `decisions` per Phase 2.

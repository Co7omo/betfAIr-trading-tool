# Execution Engine Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere l'Execution Engine baseline (modalità dry_run + paper) che consuma `decisions` con outcome=ALLOW, calcola size via Kelly fractional, piazza ordini LIMIT BACK con `customer_order_ref` idempotente, persiste lifecycle events in `orders` e match deltas in `fills`. Plus un Reconciler che polla ordini aperti come background task nello Scheduler.

**Architecture:** Sotto-package `services/` con file dedicati (`sizer.py` pure math, `execution_engine.py` orchestrator, `reconciler.py` background task). `FakeAsyncBetfairClient` esteso con `place_orders` + `list_current_orders` per matching deterministico nei test. `ExecutionMode` enum (`dry_run`, `paper`) — `live` esplicitamente NON in scope. Refactor minimo di `DecisionEngine.evaluate` per ritornare `Decision | None` invece di `UUID | None`.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, asyncpg, Alembic, Pydantic v2, testcontainers (integration).

**Reference spec:** `docs/superpowers/specs/2026-05-13-execution-engine-design.md`

---

## File Structure

```
alembic/versions/
└── 004_orders_fills.py                          # NUOVO

src/betfair_trading/
├── models/
│   └── order.py                                 # NUOVO - Pydantic contracts
├── db/
│   └── writer.py                                # + 3 functions
├── services/
│   ├── sizer.py                                 # NUOVO - pure Kelly math
│   ├── execution_engine.py                      # NUOVO - orchestrator
│   ├── reconciler.py                            # NUOVO - background task
│   ├── scheduler.py                             # + reconciler param + _reconcile_loop
│   └── decision_engine.py                       # evaluate() returns Decision | None
└── main.py                                       # Wiring ExecutionEngine + Reconciler

tests/
├── unit/
│   ├── test_order_model.py                      # NUOVO
│   └── test_sizer.py                            # NUOVO
└── integration/
    ├── conftest.py                              # TRUNCATE + orders, fills
    ├── test_pg_smoke.py                         # + orders, fills in expected
    ├── fakes/
    │   └── fake_betfair_client.py               # + place_orders, list_current_orders
    ├── test_order_writers.py                    # NUOVO
    ├── test_execution_engine.py                 # NUOVO
    ├── test_reconciler.py                       # NUOVO
    ├── test_pipeline_execution.py               # NUOVO end-to-end
    └── test_pipeline_decision.py                # update for new evaluate() signature

config/
└── trading.yaml                                  # + execution_mode, min_stake, reconcile_interval
```

---

## Pre-requisiti git

```bash
git checkout main
git pull
git checkout -b feature/execution-engine
```

---

## Task 1: Schema migration `004_orders_fills.py` + fixture updates

**Files:**
- Create: `alembic/versions/004_orders_fills.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_pg_smoke.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/004_orders_fills.py`:

```python
"""orders + fills tables for Execution Engine baseline.

Revision ID: 004
Revises: 003
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
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
    """)
    op.execute(
        "CREATE INDEX idx_orders_customer_ref ON orders (customer_order_ref, event_ts DESC);"
    )
    op.execute("CREATE INDEX idx_orders_decision ON orders (decision_id);")
    op.execute("CREATE INDEX idx_orders_market ON orders (market_id, event_ts);")

    op.execute("""
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
    """)
    op.execute(
        "CREATE INDEX idx_fills_customer_ref ON fills (customer_order_ref, fill_ts);"
    )
    op.execute("CREATE INDEX idx_fills_market ON fills (market_id, fill_ts);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fills;")
    op.execute("DROP TABLE IF EXISTS orders;")
```

- [ ] **Step 2: Update `tests/integration/test_pg_smoke.py`**

Find the `expected` set in `test_schema_tables_exist`. Add `"orders"` and `"fills"`:

```python
    expected = {
        "markets",
        "runners",
        "market_snapshots",
        "external_feature_snapshots",
        "feature_vectors",
        "config_snapshots",
        "decisions",
        "model_versions",
        "model_inferences",
        "orders",
        "fills",
    }
```

- [ ] **Step 3: Update `tests/integration/conftest.py`**

Find the TRUNCATE in `clean_db` and add `orders, fills`:

```python
    await conn.execute(
        "TRUNCATE markets, runners, market_snapshots, "
        "external_feature_snapshots, feature_vectors, config_snapshots, "
        "decisions, model_versions, model_inferences, orders, fills "
        "RESTART IDENTITY CASCADE"
    )
```

- [ ] **Step 4: Run smoke + integration suite**

Run: `uv run pytest tests/integration/test_pg_smoke.py -v -m integration`
Expected: 4 PASS.

Run: `uv run pytest -v -m integration`
Expected: 43 existing integration tests still pass.

Run: `uv run pytest -v -m "not integration"`
Expected: 76 unit tests still pass.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/004_orders_fills.py tests/integration/test_pg_smoke.py tests/integration/conftest.py
git commit -m "feat(db): add orders, fills tables for Execution Engine"
```

---

## Task 2: Pydantic contracts `models/order.py`

**Files:**
- Create: `src/betfair_trading/models/order.py`
- Create: `tests/unit/test_order_model.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_order_model.py`:

```python
"""Unit tests for Order Pydantic contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
    TradeIntent,
)


def test_trade_intent_construction():
    dec_id = uuid4()
    intent = TradeIntent(
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        price=Decimal("2.00"),
        size=Decimal("20.00"),
        customer_order_ref=dec_id.hex,
    )
    assert intent.side == OrderSide.BACK
    assert intent.customer_order_ref == dec_id.hex
    assert len(intent.customer_order_ref) == 32  # hex no dashes


def test_order_event_defaults():
    dec_id = uuid4()
    event = OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.00"),
        requested_size=Decimal("20.00"),
        status=OrderStatus.PENDING,
        event_type=OrderEventType.PLACED,
        mode=ExecutionMode.DRY_RUN,
    )
    assert event.matched_size == Decimal("0")
    assert event.average_price_matched is None
    assert event.event_ts is None  # DB default
    assert event.api_response is None


def test_fill_construction():
    dec_id = uuid4()
    fill = Fill(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        selection_id=101,
        matched_size_delta=Decimal("10.00"),
        average_price_matched=Decimal("2.04"),
        cumulative_matched_size=Decimal("10.00"),
        remaining_size=Decimal("10.00"),
    )
    assert fill.matched_size_delta == Decimal("10.00")
    assert fill.remaining_size == Decimal("10.00")


def test_execution_mode_values():
    assert ExecutionMode.DRY_RUN.value == "dry_run"
    assert ExecutionMode.PAPER.value == "paper"
    # Live is NOT defined — verify by attribute check
    assert not hasattr(ExecutionMode, "LIVE")


def test_order_status_values():
    assert OrderStatus.PENDING.value == "PENDING"
    assert OrderStatus.EXECUTABLE.value == "EXECUTABLE"
    assert OrderStatus.EXECUTION_COMPLETE.value == "EXECUTION_COMPLETE"
    assert OrderStatus.ERROR.value == "ERROR"
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_order_model.py -v -m "not integration"`
Expected: 5 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `models/order.py`**

Create `src/betfair_trading/models/order.py`:

```python
"""Pydantic contracts for orders, fills, and trade intents."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


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
    # LIVE intentionally absent — adding requires explicit enum extension + opt-in.


class TradeIntent(BaseModel):
    """Computed intent before order placement."""

    decision_id: UUID
    market_id: str
    event_id: str
    selection_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    customer_order_ref: str


class OrderEvent(BaseModel):
    """One row in `orders`. Append-only lifecycle event."""

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
    event_ts: datetime | None = None  # DB default
    api_response: dict | None = None
    mode: ExecutionMode


class Fill(BaseModel):
    """One row in `fills`. Incremental match delta."""

    fill_id: UUID = Field(default_factory=uuid4)
    customer_order_ref: str
    decision_id: UUID
    market_id: str
    selection_id: int
    fill_ts: datetime | None = None  # DB default
    matched_size_delta: Decimal
    average_price_matched: Decimal
    cumulative_matched_size: Decimal
    remaining_size: Decimal
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_order_model.py -v -m "not integration"`
Expected: 5 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 81 unit tests pass (76 + 5 new).

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/models/order.py tests/unit/test_order_model.py
git commit -m "feat(models): add OrderEvent, Fill, TradeIntent Pydantic contracts"
```

---

## Task 3: DB writers (insert_order_event, insert_fill, fetch_open_orders)

**Files:**
- Modify: `src/betfair_trading/db/writer.py`
- Create: `tests/integration/test_order_writers.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_order_writers.py`:

```python
"""Integration tests for order/fill writers."""

import json
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import (
    fetch_open_orders,
    insert_fill,
    insert_order_event,
)
from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
)


def _make_order_event(
    decision_id=None, status=OrderStatus.EXECUTABLE, mode=ExecutionMode.PAPER,
    event_type=OrderEventType.PLACED, matched_size=Decimal("0"),
):
    dec_id = decision_id or uuid4()
    return OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.0000"),
        requested_size=Decimal("20.00"),
        matched_size=matched_size,
        status=status,
        event_type=event_type,
        mode=mode,
    )


async def test_insert_order_event_persists(pg_pool: asyncpg.Pool):
    event = _make_order_event()
    async with pg_pool.acquire() as conn:
        order_event_id = await insert_order_event(conn, event)
    assert order_event_id == event.order_event_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["customer_order_ref"] == event.customer_order_ref
    assert row["status"] == "EXECUTABLE"
    assert row["mode"] == "paper"
    assert row["requested_price"] == Decimal("2.0000")


async def test_insert_fill_persists(pg_pool: asyncpg.Pool):
    dec_id = uuid4()
    fill = Fill(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        selection_id=101,
        matched_size_delta=Decimal("10.00"),
        average_price_matched=Decimal("2.04"),
        cumulative_matched_size=Decimal("10.00"),
        remaining_size=Decimal("10.00"),
    )
    async with pg_pool.acquire() as conn:
        fill_id = await insert_fill(conn, fill)
    assert fill_id == fill.fill_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fills WHERE fill_id = $1", fill_id
        )
    assert row["matched_size_delta"] == Decimal("10.00")
    assert row["average_price_matched"] == Decimal("2.0400")


async def test_fetch_open_orders_distinct_on_latest(pg_pool: asyncpg.Pool):
    """Two events for the same customer_order_ref: latest wins, only one row returned."""
    dec_id = uuid4()
    e1 = _make_order_event(decision_id=dec_id, status=OrderStatus.PENDING,
                            event_type=OrderEventType.PLACED)
    e2 = _make_order_event(decision_id=dec_id, status=OrderStatus.EXECUTABLE,
                            event_type=OrderEventType.LIFECYCLE,
                            matched_size=Decimal("0"))

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, e1)
        await insert_order_event(conn, e2)

        open_orders = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)

    assert len(open_orders) == 1
    assert open_orders[0].status == OrderStatus.EXECUTABLE  # latest
    assert open_orders[0].customer_order_ref == dec_id.hex


async def test_fetch_open_orders_filters_terminal(pg_pool: asyncpg.Pool):
    """Orders in EXECUTION_COMPLETE are NOT returned."""
    e1 = _make_order_event(status=OrderStatus.EXECUTION_COMPLETE)
    e2 = _make_order_event(status=OrderStatus.PENDING)
    e3 = _make_order_event(status=OrderStatus.EXECUTABLE)

    async with pg_pool.acquire() as conn:
        for e in (e1, e2, e3):
            await insert_order_event(conn, e)
        open_orders = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)

    statuses = {o.status for o in open_orders}
    assert OrderStatus.EXECUTION_COMPLETE not in statuses
    assert OrderStatus.PENDING in statuses
    assert OrderStatus.EXECUTABLE in statuses


async def test_fetch_open_orders_filters_by_mode(pg_pool: asyncpg.Pool):
    """Only orders matching the given mode are returned."""
    e_paper = _make_order_event(mode=ExecutionMode.PAPER)
    e_dry = _make_order_event(mode=ExecutionMode.DRY_RUN)

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, e_paper)
        await insert_order_event(conn, e_dry)
        paper_only = await fetch_open_orders(conn, mode=ExecutionMode.PAPER)
        dry_only = await fetch_open_orders(conn, mode=ExecutionMode.DRY_RUN)

    assert len(paper_only) == 1 and paper_only[0].mode == ExecutionMode.PAPER
    assert len(dry_only) == 1 and dry_only[0].mode == ExecutionMode.DRY_RUN
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_order_writers.py -v -m integration`
Expected: 5 FAIL with `ImportError`.

- [ ] **Step 3: Add writers to `db/writer.py`**

Open `src/betfair_trading/db/writer.py`.

Add import near the other model imports:

```python
from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
)
```

Append at the end of the file:

```python
async def insert_order_event(
    conn: asyncpg.Connection, event: OrderEvent
) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO orders
           (order_event_id, customer_order_ref, decision_id, market_id, event_id,
            selection_id, side, requested_price, requested_size,
            matched_size, average_price_matched, status, event_type,
            api_response, mode)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
           RETURNING order_event_id""",
        event.order_event_id,
        event.customer_order_ref,
        event.decision_id,
        event.market_id,
        event.event_id,
        event.selection_id,
        event.side.value,
        event.requested_price,
        event.requested_size,
        event.matched_size,
        event.average_price_matched,
        event.status.value,
        event.event_type.value,
        json.dumps(event.api_response, default=str) if event.api_response else None,
        event.mode.value,
    )


async def insert_fill(conn: asyncpg.Connection, fill: Fill) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO fills
           (fill_id, customer_order_ref, decision_id, market_id, selection_id,
            matched_size_delta, average_price_matched,
            cumulative_matched_size, remaining_size)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           RETURNING fill_id""",
        fill.fill_id,
        fill.customer_order_ref,
        fill.decision_id,
        fill.market_id,
        fill.selection_id,
        fill.matched_size_delta,
        fill.average_price_matched,
        fill.cumulative_matched_size,
        fill.remaining_size,
    )


async def fetch_open_orders(
    conn: asyncpg.Connection, mode: ExecutionMode
) -> list[OrderEvent]:
    """Return the current state of every order that is still open (PENDING or
    EXECUTABLE), filtered by execution mode."""
    rows = await conn.fetch(
        """
        WITH latest AS (
          SELECT DISTINCT ON (customer_order_ref)
            order_event_id, customer_order_ref, decision_id, market_id, event_id,
            selection_id, side, requested_price, requested_size,
            matched_size, average_price_matched, status, event_type,
            event_ts, api_response, mode
          FROM orders
          WHERE mode = $1
          ORDER BY customer_order_ref, event_ts DESC
        )
        SELECT * FROM latest
        WHERE status IN ('PENDING', 'EXECUTABLE')
        """,
        mode.value,
    )
    return [
        OrderEvent(
            order_event_id=r["order_event_id"],
            customer_order_ref=r["customer_order_ref"],
            decision_id=r["decision_id"],
            market_id=r["market_id"],
            event_id=r["event_id"],
            selection_id=r["selection_id"],
            side=OrderSide(r["side"]),
            requested_price=r["requested_price"],
            requested_size=r["requested_size"],
            matched_size=r["matched_size"],
            average_price_matched=r["average_price_matched"],
            status=OrderStatus(r["status"]),
            event_type=OrderEventType(r["event_type"]),
            event_ts=r["event_ts"],
            api_response=(
                json.loads(r["api_response"])
                if isinstance(r["api_response"], str)
                else r["api_response"]
            ),
            mode=ExecutionMode(r["mode"]),
        )
        for r in rows
    ]
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_order_writers.py -v -m integration`
Expected: 5 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m integration`
Expected: 48 integration tests pass (43 + 5 new).

Run: `uv run pytest -v -m "not integration"`
Expected: 81 unit tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/db/writer.py tests/integration/test_order_writers.py
git commit -m "feat(db): add insert_order_event, insert_fill, fetch_open_orders"
```

---

## Task 4: Pure Kelly sizer (`services/sizer.py`)

**Files:**
- Create: `src/betfair_trading/services/sizer.py`
- Create: `tests/unit/test_sizer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sizer.py`:

```python
"""Unit tests for the pure Kelly sizer."""

import math
from decimal import Decimal

from betfair_trading.services.sizer import compute_stake, kelly_fraction


def test_kelly_fraction_positive_edge():
    # p=0.55, o=2.0 → (0.55*2 - 1) / (2 - 1) = 0.10
    assert math.isclose(kelly_fraction(0.55, 2.0), 0.10, abs_tol=1e-9)


def test_kelly_fraction_zero_when_negative_edge():
    # p=0.40, o=2.0 → negative → clamp to 0
    assert kelly_fraction(0.40, 2.0) == 0.0


def test_kelly_fraction_zero_when_odds_le_one():
    assert kelly_fraction(0.5, 1.0) == 0.0
    assert kelly_fraction(0.5, 0.5) == 0.0


def test_compute_stake_capped_at_max_fraction():
    # Without cap: 1000 * 0.25 * 0.10 = 25.0
    # With cap: 1000 * 0.02 = 20.0
    stake = compute_stake(
        bankroll=1000.0, p_model=0.55, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake == Decimal("20.00")


def test_compute_stake_below_min_returns_none():
    # Bankroll=10, kelly_mult=0.25, p=0.55, o=2.0
    # raw = 10 * 0.25 * 0.10 = 0.25 → below min_stake=2.0
    stake = compute_stake(
        bankroll=10.0, p_model=0.55, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake is None


def test_compute_stake_zero_kelly_returns_none():
    # Negative edge → kelly_fraction=0 → stake=0 → < min → None
    stake = compute_stake(
        bankroll=1000.0, p_model=0.40, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake is None
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_sizer.py -v -m "not integration"`
Expected: 6 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `services/sizer.py`**

Create `src/betfair_trading/services/sizer.py`:

```python
"""Pure Kelly sizing math: fractional Kelly with cap and minimum stake."""

from decimal import Decimal


def kelly_fraction(p_model: float, odds: float) -> float:
    """f* = (p*o - 1) / (o - 1).

    Returns 0.0 if odds <= 1.0 (no payoff) or if f* < 0 (no edge → no trade).
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
    """Compute the stake to place.

    raw_stake  = bankroll * kelly_multiplier * kelly_fraction(p_model, odds)
    capped     = min(raw_stake, bankroll * max_stake_fraction)
    Returns Decimal(rounded 2dp) if capped >= min_stake, else None.
    """
    f = kelly_fraction(p_model, odds)
    raw_stake = bankroll * kelly_multiplier * f
    cap = bankroll * max_stake_fraction
    capped = min(raw_stake, cap)
    if capped < min_stake:
        return None
    return Decimal(str(round(capped, 2)))
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_sizer.py -v -m "not integration"`
Expected: 6 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 87 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/sizer.py tests/unit/test_sizer.py
git commit -m "feat(execution): add pure Kelly sizer (compute_stake + kelly_fraction)"
```

---

## Task 5: Extend `FakeAsyncBetfairClient` with order methods

**Files:**
- Modify: `tests/integration/fakes/fake_betfair_client.py`

This task has no separate test file — the new methods are exercised by Task 7, 8, 9 integration tests. We add them here so subsequent tasks can use them.

- [ ] **Step 1: Add the new methods to the Fake**

Open `tests/integration/fakes/fake_betfair_client.py`.

Add to the class (preserve all existing methods):

```python
    # ------------------------------------------------------------------
    # Order surface for ExecutionEngine / Reconciler tests
    # ------------------------------------------------------------------

    def __init__(self):
        # Preserve all existing initializations; add:
        # (Note: if __init__ already exists, merge these lines into it)
        self._catalogue: list[MarketCatalogue] = []
        self._book_queues: dict[str, list[MarketSnapshotBundle]] = {}
        self._book_call_count: dict[str, int] = {}
        self._placed_orders: dict[str, dict] = {}
        self._matching_behavior: dict[str, str] = {}

    def queue_match_behavior(self, customer_order_ref: str, behavior: str) -> None:
        """Configure matching for a customer_order_ref before it is placed.

        Valid behaviors:
            'instant_match' - fully matched on first poll
            'partial'       - 50% matched on first poll, remaining stays open
            'no_match'      - never matched
            'lapse'         - status transitions to LAPSED on first poll
        """
        if behavior not in {"instant_match", "partial", "no_match", "lapse"}:
            raise ValueError(f"unknown behavior: {behavior}")
        self._matching_behavior[customer_order_ref] = behavior

    async def place_orders(
        self,
        market_id: str,
        customer_order_ref: str,
        selection_id: int,
        side: str,
        price,
        size,
        persistence_type: str = "LAPSE",
    ) -> dict:
        """Record the placement and return a synthetic instruction report."""
        if customer_order_ref in self._placed_orders:
            return {
                "status": "FAILURE",
                "error_code": "DUPLICATE_BETIDS",
                "order_status": "ERROR",
            }
        record = {
            "market_id": market_id,
            "selection_id": selection_id,
            "side": side,
            "price": float(price),
            "size": float(size),
            "persistence_type": persistence_type,
            "size_matched": 0.0,
            "size_remaining": float(size),
            "average_price_matched": 0.0,
            "order_status": "EXECUTABLE",
            "bet_id": f"FAKE-{customer_order_ref[:8]}",
        }
        self._placed_orders[customer_order_ref] = record
        return {
            "status": "SUCCESS",
            "order_status": "EXECUTABLE",
            "bet_id": record["bet_id"],
            "size_matched": 0.0,
            "average_price_matched": 0.0,
            "customer_order_ref": customer_order_ref,
        }

    async def list_current_orders(
        self, customer_order_refs: list[str]
    ) -> list[dict]:
        """Return synthetic state for placed orders, applying configured behavior."""
        out = []
        for ref in customer_order_refs:
            record = self._placed_orders.get(ref)
            if record is None:
                continue
            behavior = self._matching_behavior.get(ref, "no_match")
            requested_size = record["size"]
            requested_price = record["price"]

            if behavior == "instant_match":
                record["size_matched"] = requested_size
                record["size_remaining"] = 0.0
                record["average_price_matched"] = requested_price
                record["order_status"] = "EXECUTION_COMPLETE"
            elif behavior == "partial":
                record["size_matched"] = requested_size / 2.0
                record["size_remaining"] = requested_size / 2.0
                record["average_price_matched"] = requested_price
                # status remains EXECUTABLE
            elif behavior == "lapse":
                record["order_status"] = "LAPSED"
            # 'no_match' leaves state unchanged

            out.append({
                "customer_order_ref": ref,
                "order_status": record["order_status"],
                "size_matched": record["size_matched"],
                "size_remaining": record["size_remaining"],
                "average_price_matched": record["average_price_matched"],
                "bet_id": record["bet_id"],
            })
        return out
```

If `__init__` already exists with the catalogue/book state, MERGE the new fields (`_placed_orders`, `_matching_behavior`) into it — do NOT replace the existing fields.

- [ ] **Step 2: Verify nothing is broken**

The new methods are purely additive. Run smoke:

Run: `uv run pytest -v -m integration`
Expected: 48 PASS (Task 3 added 5 — no new regressions).

Run: `uv run pytest -v -m "not integration"`
Expected: 87 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/fakes/fake_betfair_client.py
git commit -m "test: extend FakeAsyncBetfairClient with place_orders + list_current_orders"
```

---

## Task 6: Refactor `DecisionEngine.evaluate` to return `Decision | None`

**Files:**
- Modify: `src/betfair_trading/services/decision_engine.py`
- Modify: `tests/integration/test_pipeline_decision.py` (update assertions)

- [ ] **Step 1: Modify `DecisionEngine.evaluate`**

Open `src/betfair_trading/services/decision_engine.py`. Find the `evaluate` method.

Change the return type annotation from `uuid.UUID | None` to `Decision | None`.

Change the return statement at the end of the method. Find:

```python
            decision_id = await insert_decision(conn, decision)

        log.info(
            "decision_made",
            market_id=bundle.market_id,
            outcome=outcome.value,
            selected_runner=selected_runner_id,
            edge_net=selected_edge_net,
        )
        return decision_id
```

Replace with:

```python
            await insert_decision(conn, decision)

        log.info(
            "decision_made",
            market_id=bundle.market_id,
            outcome=outcome.value,
            selected_runner=selected_runner_id,
            edge_net=selected_edge_net,
        )
        return decision
```

And replace the early return for no-runner-metadata:

```python
            if not runners_meta:
                log.warning("decision_skip_no_runner_meta", market_id=bundle.market_id)
                return None
```

(This stays as `return None` — no change.)

The return type annotation on the method signature changes:

```python
    async def evaluate(
        self,
        bundle: MarketSnapshotBundle,
        snapshot_ids: list[uuid.UUID],
        feature_vector_ids: list[uuid.UUID],
    ) -> Decision | None:
```

- [ ] **Step 2: Update existing test assertions**

Open `tests/integration/test_pipeline_decision.py`. The existing tests use `engine.evaluate(...)` either via the `_run_pipeline` helper (which discards the return value) or implicitly through the callback.

Find the callback in `_run_pipeline`:

```python
    decisions_made = []

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            did = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
            decisions_made.append(did)
```

Change `did` (was UUID) to `dec` (now Decision):

```python
    decisions_made = []

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            dec = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
            decisions_made.append(dec)
```

The downstream tests query `decisions` table directly for the assertions, so the change is purely the variable name. Verify no test reads `.decision_id` on the captured value or treats it as UUID.

Also check `test_position_limit_blocks_second_allow`:

```python
    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            await engine.evaluate(bundle, snapshot_ids, fv_ids)
```

This one discards the return — no change needed.

- [ ] **Step 3: Run integration tests**

Run: `uv run pytest tests/integration/test_pipeline_decision.py -v -m integration`
Expected: 7 PASS.

Run: `uv run pytest -v -m integration`
Expected: 48 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/betfair_trading/services/decision_engine.py tests/integration/test_pipeline_decision.py
git commit -m "refactor(decision): evaluate() returns Decision | None instead of UUID | None"
```

---

## Task 7: ExecutionEngine + 5 integration tests

**Files:**
- Create: `src/betfair_trading/services/execution_engine.py`
- Create: `tests/integration/test_execution_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_execution_engine.py`:

```python
"""End-to-end tests for ExecutionEngine."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from betfair_trading.db.writer import insert_decision, insert_market_snapshots
from betfair_trading.models.decision import (
    Decision,
    DecisionOutcome,
    GateResult,
)
from betfair_trading.models.market import (
    MarketSnapshotBundle,
    RunnerSnapshot,
)
from betfair_trading.models.order import ExecutionMode, OrderEventType, OrderStatus
from betfair_trading.services.execution_engine import ExecutionEngine
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient


def _make_decision(outcome=DecisionOutcome.ALLOW, market_id="1.A", event_id="E-A",
                   selected_runner_id=101, p_model_home=0.55):
    fv_id = uuid4()
    return Decision(
        market_id=market_id,
        event_id=event_id,
        decision_ts=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: p_model_home, 102: 0.25, 103: 1.0 - p_model_home - 0.25},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: p_model_home - 0.5, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=selected_runner_id,
        selected_edge_net=Decimal("0.022500"),
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=outcome,
        feature_vector_ids=[fv_id],
    )


async def _seed_market_snapshot(pg_pool, market_id, runner_id, best_back_price):
    """Insert a market_snapshot so ExecutionEngine can read the latest quote."""
    bundle = MarketSnapshotBundle(
        market_id=market_id, event_id="E-A",
        snapshot_ts=datetime.now(UTC),
        runners=[
            RunnerSnapshot(
                runner_id=runner_id,
                best_back_price=Decimal(str(best_back_price)),
                best_lay_price=Decimal(str(best_back_price + 0.04)),
                traded_volume=Decimal("0"),
            ),
        ],
        market_status="OPEN", inplay=False,
        total_matched=Decimal("1000"), minutes_to_start=60.0,
    )
    # Also insert market/runners parent rows (FK requirements differ across migrations,
    # but markets/runners aren't required by orders schema — only market_snapshots).
    async with pg_pool.acquire() as conn:
        await insert_market_snapshots(conn, bundle)


async def test_dry_run_writes_placed_event_no_api_call(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    # Persist the decision so foreign-key style relationships are satisfied at audit level
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=1000.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is not None
    # No API call was made
    assert fake._placed_orders == {}

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["mode"] == "dry_run"
    assert row["event_type"] == "PLACED"
    assert row["status"] == "PENDING"
    assert row["api_response"] is None
    assert row["customer_order_ref"] == decision.decision_id.hex


async def test_paper_mode_calls_fake_client_writes_lifecycle(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.PAPER,
        bankroll=1000.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is not None
    assert decision.decision_id.hex in fake._placed_orders

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_event_id = $1", order_event_id
        )
    assert row["mode"] == "paper"
    assert row["status"] == "EXECUTABLE"
    assert row["api_response"] is not None


async def test_sizing_below_min_stake_skips_order(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    # Bankroll=10, kelly_mult=0.25 → max stake = 0.20, far below min_stake=2.0
    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=10.0,
        min_stake=2.0,
    )
    order_event_id = await engine.on_decision_allow(decision)

    assert order_event_id is None
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM orders")
    assert count == 0


async def test_customer_order_ref_is_decision_id_hex(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision()
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)
    async with pg_pool.acquire() as conn:
        await insert_decision(conn, decision)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.DRY_RUN,
        bankroll=1000.0,
    )
    await engine.on_decision_allow(decision)

    async with pg_pool.acquire() as conn:
        ref = await conn.fetchval(
            "SELECT customer_order_ref FROM orders LIMIT 1"
        )
    assert ref == decision.decision_id.hex
    assert len(ref) == 32  # UUID hex no dashes


async def test_block_outcome_does_not_trigger_execution(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    decision = _make_decision(outcome=DecisionOutcome.BLOCK_SOFT)
    await _seed_market_snapshot(pg_pool, decision.market_id, 101, 2.0)

    engine = ExecutionEngine(
        pool=pg_pool, bf_client=fake,
        mode=ExecutionMode.PAPER,
        bankroll=1000.0,
    )
    result = await engine.on_decision_allow(decision)

    assert result is None
    assert fake._placed_orders == {}
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM orders")
    assert count == 0
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_execution_engine.py -v -m integration`
Expected: 5 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `services/execution_engine.py`**

Create `src/betfair_trading/services/execution_engine.py`:

```python
"""ExecutionEngine: receives ALLOW decisions, computes Kelly-sized intent,
places orders (dry_run or paper), persists lifecycle events.
"""

import uuid
from decimal import Decimal

import asyncpg
import structlog

from betfair_trading.db.writer import insert_order_event
from betfair_trading.models.decision import Decision, DecisionOutcome
from betfair_trading.models.order import (
    ExecutionMode,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
    TradeIntent,
)
from betfair_trading.services.sizer import compute_stake

log = structlog.get_logger()


class ExecutionEngine:
    def __init__(
        self,
        pool: asyncpg.Pool,
        bf_client,
        mode: ExecutionMode = ExecutionMode.DRY_RUN,
        bankroll: float = 1000.0,
        kelly_multiplier: float = 0.25,
        max_stake_fraction: float = 0.02,
        min_stake: float = 2.0,
    ):
        self._pool = pool
        self._bf = bf_client
        self._mode = mode
        self._bankroll = bankroll
        self._kelly_multiplier = kelly_multiplier
        self._max_stake_fraction = max_stake_fraction
        self._min_stake = min_stake

    async def on_decision_allow(self, decision: Decision) -> uuid.UUID | None:
        """If decision.decision_outcome == ALLOW, build intent + place + persist.
        Returns the order_event_id (PLACED event) or None if skipped."""
        if decision.decision_outcome != DecisionOutcome.ALLOW:
            return None
        if decision.selected_runner_id is None:
            return None

        async with self._pool.acquire() as conn:
            quote_row = await conn.fetchrow(
                "SELECT best_back_price FROM market_snapshots "
                "WHERE market_id = $1 AND runner_id = $2 "
                "ORDER BY snapshot_ts DESC LIMIT 1",
                decision.market_id, decision.selected_runner_id,
            )
            if quote_row is None or quote_row["best_back_price"] is None:
                log.warning(
                    "execution_skip_no_quote",
                    market_id=decision.market_id,
                    runner_id=decision.selected_runner_id,
                )
                return None
            odds = float(quote_row["best_back_price"])

            p_model = decision.p_model.get(decision.selected_runner_id, 0.0)

            stake = compute_stake(
                bankroll=self._bankroll,
                p_model=p_model, odds=odds,
                kelly_multiplier=self._kelly_multiplier,
                max_stake_fraction=self._max_stake_fraction,
                min_stake=self._min_stake,
            )
            if stake is None:
                log.info(
                    "execution_skip_below_min_stake",
                    market_id=decision.market_id, p_model=p_model, odds=odds,
                )
                return None

            customer_order_ref = decision.decision_id.hex
            intent = TradeIntent(
                decision_id=decision.decision_id,
                market_id=decision.market_id,
                event_id=decision.event_id,
                selection_id=decision.selected_runner_id,
                side=OrderSide.BACK,
                price=Decimal(str(odds)),
                size=stake,
                customer_order_ref=customer_order_ref,
            )

            event = await self._build_and_place_event(intent)
            order_event_id = await insert_order_event(conn, event)

        log.info(
            "order_placed",
            customer_order_ref=customer_order_ref,
            mode=self._mode.value,
            status=event.status.value,
            size=float(stake),
        )
        return order_event_id

    async def _build_and_place_event(self, intent: TradeIntent) -> OrderEvent:
        """Either log-only (dry_run) or call bf_client.place_orders (paper)."""
        if self._mode == ExecutionMode.DRY_RUN:
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                status=OrderStatus.PENDING,
                event_type=OrderEventType.PLACED,
                api_response=None,
                mode=ExecutionMode.DRY_RUN,
            )

        # PAPER mode
        try:
            response = await self._bf.place_orders(
                market_id=intent.market_id,
                customer_order_ref=intent.customer_order_ref,
                selection_id=intent.selection_id,
                side=intent.side.value,
                price=intent.price,
                size=intent.size,
            )
            if response.get("status") == "SUCCESS":
                order_status = OrderStatus(
                    response.get("order_status", OrderStatus.EXECUTABLE.value)
                )
            else:
                order_status = OrderStatus.ERROR

            avg = response.get("average_price_matched")
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                matched_size=Decimal(str(response.get("size_matched", 0))),
                average_price_matched=Decimal(str(avg)) if avg else None,
                status=order_status,
                event_type=OrderEventType.PLACED,
                api_response=response,
                mode=ExecutionMode.PAPER,
            )
        except Exception as e:
            log.exception(
                "execution_place_error",
                market_id=intent.market_id,
                customer_order_ref=intent.customer_order_ref,
            )
            return OrderEvent(
                customer_order_ref=intent.customer_order_ref,
                decision_id=intent.decision_id,
                market_id=intent.market_id,
                event_id=intent.event_id,
                selection_id=intent.selection_id,
                side=intent.side,
                requested_price=intent.price,
                requested_size=intent.size,
                status=OrderStatus.ERROR,
                event_type=OrderEventType.ERROR,
                api_response={"error": str(e)},
                mode=ExecutionMode.PAPER,
            )
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_execution_engine.py -v -m integration`
Expected: 5 PASS.

If `test_paper_mode_calls_fake_client_writes_lifecycle` fails because the fake returns status="EXECUTABLE" but the ExecutionEngine expected something else, verify the Fake's `place_orders` response matches the spec ("status": "SUCCESS", "order_status": "EXECUTABLE").

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m integration`
Expected: 53 integration tests pass.

Run: `uv run pytest -v -m "not integration"`
Expected: 87 unit tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/execution_engine.py tests/integration/test_execution_engine.py
git commit -m "feat(execution): add ExecutionEngine with dry_run + paper modes"
```

---

## Task 8: Reconciler + 5 integration tests

**Files:**
- Create: `src/betfair_trading/services/reconciler.py`
- Create: `tests/integration/test_reconciler.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_reconciler.py`:

```python
"""Integration tests for Reconciler."""

from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_order_event
from betfair_trading.models.order import (
    ExecutionMode,
    OrderEvent,
    OrderEventType,
    OrderSide,
    OrderStatus,
)
from betfair_trading.services.reconciler import Reconciler
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient


def _make_event(decision_id=None, status=OrderStatus.EXECUTABLE,
                event_type=OrderEventType.PLACED, matched=Decimal("0"),
                mode=ExecutionMode.PAPER, requested_size=Decimal("20.00")):
    dec_id = decision_id or uuid4()
    return OrderEvent(
        customer_order_ref=dec_id.hex,
        decision_id=dec_id,
        market_id="1.A",
        event_id="E-A",
        selection_id=101,
        side=OrderSide.BACK,
        requested_price=Decimal("2.0000"),
        requested_size=requested_size,
        matched_size=matched,
        status=status,
        event_type=event_type,
        mode=mode,
    )


async def test_reconcile_no_open_orders_returns_zero(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)
    count = await rec.reconcile_open_orders()
    assert count == 0


async def test_reconcile_instant_match_writes_fill_and_lifecycle(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    # Seed: a placed order in EXECUTABLE state
    event = _make_event(requested_size=Decimal("20.00"))
    # Pretend it was placed via the fake (so fake has it tracked)
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id,
        "selection_id": event.selection_id,
        "side": "BACK",
        "price": 2.0,
        "size": 20.0,
        "persistence_type": "LAPSE",
        "size_matched": 0.0,
        "size_remaining": 20.0,
        "average_price_matched": 0.0,
        "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "instant_match")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    count = await rec.reconcile_open_orders()
    assert count == 1

    async with pg_pool.acquire() as conn:
        fills = await conn.fetch("SELECT * FROM fills")
        lifecycle_events = await conn.fetch(
            "SELECT * FROM orders WHERE event_type = 'LIFECYCLE'"
        )

    assert len(fills) == 1
    assert fills[0]["matched_size_delta"] == Decimal("20.00")
    assert fills[0]["cumulative_matched_size"] == Decimal("20.00")
    assert fills[0]["remaining_size"] == Decimal("0")

    assert len(lifecycle_events) == 1
    assert lifecycle_events[0]["status"] == "EXECUTION_COMPLETE"


async def test_reconcile_partial_match_writes_delta(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event(requested_size=Decimal("20.00"))
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id, "selection_id": event.selection_id,
        "side": "BACK", "price": 2.0, "size": 20.0, "persistence_type": "LAPSE",
        "size_matched": 0.0, "size_remaining": 20.0,
        "average_price_matched": 0.0, "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "partial")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        fill = await conn.fetchrow("SELECT * FROM fills")
        latest = await conn.fetchrow(
            "SELECT status, matched_size FROM orders "
            "ORDER BY event_ts DESC LIMIT 1"
        )

    assert fill["matched_size_delta"] == Decimal("10.00")
    assert fill["remaining_size"] == Decimal("10.00")
    # Status remains EXECUTABLE for partial match
    assert latest["status"] == "EXECUTABLE"
    assert latest["matched_size"] == Decimal("10.00")


async def test_reconcile_no_change_writes_nothing(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event()
    fake._placed_orders[event.customer_order_ref] = {
        "market_id": event.market_id, "selection_id": event.selection_id,
        "side": "BACK", "price": 2.0, "size": 20.0, "persistence_type": "LAPSE",
        "size_matched": 0.0, "size_remaining": 20.0,
        "average_price_matched": 0.0, "order_status": "EXECUTABLE",
        "bet_id": "FAKE-TEST",
    }
    fake.queue_match_behavior(event.customer_order_ref, "no_match")

    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)
        before_count = await conn.fetchval("SELECT COUNT(*) FROM orders")

    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        after_count = await conn.fetchval("SELECT COUNT(*) FROM orders")
        fills = await conn.fetchval("SELECT COUNT(*) FROM fills")

    assert after_count == before_count  # no new lifecycle event
    assert fills == 0


async def test_reconcile_terminal_state_not_picked_up(pg_pool: asyncpg.Pool):
    """An order in EXECUTION_COMPLETE is NOT reconciled again."""
    fake = FakeAsyncBetfairClient()
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=ExecutionMode.PAPER)

    event = _make_event(status=OrderStatus.EXECUTION_COMPLETE)
    async with pg_pool.acquire() as conn:
        await insert_order_event(conn, event)

    count = await rec.reconcile_open_orders()
    assert count == 0
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_reconciler.py -v -m integration`
Expected: 5 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `services/reconciler.py`**

Create `src/betfair_trading/services/reconciler.py`:

```python
"""Reconciler: background task polling open orders, writing lifecycle + fills."""

from decimal import Decimal

import asyncpg
import structlog

from betfair_trading.db.writer import (
    fetch_open_orders,
    insert_fill,
    insert_order_event,
)
from betfair_trading.models.order import (
    ExecutionMode,
    Fill,
    OrderEvent,
    OrderEventType,
    OrderStatus,
)

log = structlog.get_logger()


class Reconciler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        bf_client,
        mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ):
        self._pool = pool
        self._bf = bf_client
        self._mode = mode

    async def reconcile_open_orders(self) -> int:
        """Return the number of orders processed."""
        async with self._pool.acquire() as conn:
            open_orders = await fetch_open_orders(conn, mode=self._mode)
            if not open_orders:
                return 0

            # DRY_RUN: count open orders but skip API + write
            if self._mode == ExecutionMode.DRY_RUN:
                return len(open_orders)

            refs = [o.customer_order_ref for o in open_orders]
            current = await self._bf.list_current_orders(refs)
            current_by_ref = {c["customer_order_ref"]: c for c in current}

            for prev in open_orders:
                cur = current_by_ref.get(prev.customer_order_ref)
                if cur is None:
                    continue

                new_matched = Decimal(str(cur.get("size_matched", 0)))
                new_status_str = cur.get("order_status", prev.status.value)
                new_status = OrderStatus(new_status_str)
                matched_delta = new_matched - prev.matched_size

                avg_price = cur.get("average_price_matched")
                avg_decimal = Decimal(str(avg_price)) if avg_price else None

                if matched_delta > 0:
                    remaining = Decimal(str(cur.get("size_remaining", 0)))
                    await insert_fill(conn, Fill(
                        customer_order_ref=prev.customer_order_ref,
                        decision_id=prev.decision_id,
                        market_id=prev.market_id,
                        selection_id=prev.selection_id,
                        matched_size_delta=matched_delta,
                        average_price_matched=avg_decimal or Decimal("0"),
                        cumulative_matched_size=new_matched,
                        remaining_size=remaining,
                    ))

                if matched_delta > 0 or new_status != prev.status:
                    await insert_order_event(conn, OrderEvent(
                        customer_order_ref=prev.customer_order_ref,
                        decision_id=prev.decision_id,
                        market_id=prev.market_id,
                        event_id=prev.event_id,
                        selection_id=prev.selection_id,
                        side=prev.side,
                        requested_price=prev.requested_price,
                        requested_size=prev.requested_size,
                        matched_size=new_matched,
                        average_price_matched=avg_decimal,
                        status=new_status,
                        event_type=OrderEventType.LIFECYCLE,
                        api_response=cur,
                        mode=prev.mode,
                    ))

        log.debug(
            "reconcile_complete",
            count=len(open_orders),
            mode=self._mode.value,
        )
        return len(open_orders)
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_reconciler.py -v -m integration`
Expected: 5 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m integration`
Expected: 58 integration tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/reconciler.py tests/integration/test_reconciler.py
git commit -m "feat(execution): add Reconciler polling open orders + writing fills"
```

---

## Task 9: Scheduler extension (reconcile_loop)

**Files:**
- Modify: `src/betfair_trading/services/scheduler.py`

This task has no separate test file — the existing `test_pipeline_decision.py` and the next task's `test_pipeline_execution.py` exercise the Scheduler.

- [ ] **Step 1: Extend the Scheduler**

Open `src/betfair_trading/services/scheduler.py`.

Update the `__init__` signature to accept `reconciler` and `reconcile_interval`:

```python
class Scheduler:
    def __init__(
        self,
        collector: MarketCollector,
        bf_client,
        poll_interval: int = 10,
        discovery_interval: int = 300,
        keepalive_interval: int = 3600,
        reconciler=None,                # NEW
        reconcile_interval: int = 10,    # NEW
    ):
        self._collector = collector
        self._bf_client = bf_client
        self._poll_interval = poll_interval
        self._discovery_interval = discovery_interval
        self._keepalive_interval = keepalive_interval
        self._reconciler = reconciler
        self._reconcile_interval = reconcile_interval
        self._running = False
        self._on_snapshot = None
```

Update the `run` method to conditionally include the reconcile task:

```python
    async def run(self) -> None:
        self._running = True
        log.info(
            "scheduler_started",
            poll_interval=self._poll_interval,
            discovery_interval=self._discovery_interval,
        )

        tasks = [
            asyncio.create_task(self._discovery_loop(), name="discovery"),
            asyncio.create_task(self._poll_loop(), name="polling"),
            asyncio.create_task(self._keepalive_loop(), name="keepalive"),
        ]
        if self._reconciler is not None:
            tasks.append(
                asyncio.create_task(self._reconcile_loop(), name="reconcile")
            )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("scheduler_cancelled")
        finally:
            self._running = False
            for task in tasks:
                task.cancel()
```

Add the `_reconcile_loop` method (after `_keepalive_loop`):

```python
    async def _reconcile_loop(self) -> None:
        while self._running:
            try:
                await self._reconciler.reconcile_open_orders()
            except Exception:
                log.exception("reconcile_error")
            await asyncio.sleep(self._reconcile_interval)
```

- [ ] **Step 2: Verify no regressions**

Run: `uv run pytest -v -m integration`
Expected: 58 PASS. The existing tests instantiate `Scheduler(...)` WITHOUT `reconciler` — the new default `None` preserves Phase 2 behavior.

Run: `uv run pytest -v -m "not integration"`
Expected: 87 PASS.

- [ ] **Step 3: Commit**

```bash
git add src/betfair_trading/services/scheduler.py
git commit -m "feat(scheduler): add optional reconciler + _reconcile_loop background task"
```

---

## Task 10: End-to-end pipeline test + wire `main.py`

**Files:**
- Create: `tests/integration/test_pipeline_execution.py`
- Modify: `src/betfair_trading/main.py`
- Modify: `config/trading.yaml`

- [ ] **Step 1: Write the failing end-to-end test**

Create `tests/integration/test_pipeline_execution.py`:

```python
"""End-to-end pipeline test: collector → fb → decision → execution → reconcile."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.models.decision import DecisionOutcome
from betfair_trading.models.order import ExecutionMode
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.execution_engine import ExecutionEngine
from betfair_trading.services.external_ingestor import ExternalDataIngestor
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.probability_providers import BiasedStubProvider
from betfair_trading.services.reconciler import Reconciler
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def _build_pipeline(pg_pool, mode: ExecutionMode):
    """Construct collector, feature_builder, decision_engine, execution_engine, reconciler."""
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal",
                                start_time=datetime.now(UTC) + timedelta(minutes=60)))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    provider = BiasedStubProvider(home_bias=0.05)
    de = DecisionEngine(
        pool=pg_pool, provider=provider,
        edge_threshold=0.02, min_liquidity=100.0, max_spread=0.10,
        max_positions_per_event=1,
    )
    ee = ExecutionEngine(
        pool=pg_pool, bf_client=fake, mode=mode,
        bankroll=1000.0, min_stake=2.0,
    )
    rec = Reconciler(pool=pg_pool, bf_client=fake, mode=mode)
    return fake, collector, fb, de, ee, rec


async def test_full_pipeline_paper_allow_to_placed_order(pg_pool: asyncpg.Pool):
    fake, collector, fb, de, ee, rec = await _build_pipeline(pg_pool, ExecutionMode.PAPER)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await de.evaluate(bundle, snapshot_ids, fv_ids)
        if decision and decision.decision_outcome == DecisionOutcome.ALLOW:
            order_event_id = await ee.on_decision_allow(decision)
            # Pre-configure the fake to instantly match the placed order
            if order_event_id is not None:
                fake.queue_match_behavior(decision.decision_id.hex, "instant_match")

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM orders ORDER BY event_ts")
        fills = await conn.fetch("SELECT * FROM fills")

    # Expect at least: 1 PLACED + 1 LIFECYCLE event after reconcile
    placed = [o for o in orders if o["event_type"] == "PLACED"]
    lifecycle = [o for o in orders if o["event_type"] == "LIFECYCLE"]
    assert len(placed) == 1
    assert placed[0]["mode"] == "paper"
    assert placed[0]["status"] == "EXECUTABLE"
    assert len(lifecycle) == 1
    assert lifecycle[0]["status"] == "EXECUTION_COMPLETE"
    assert len(fills) == 1


async def test_dry_run_pipeline_writes_pending_no_fills(pg_pool: asyncpg.Pool):
    fake, collector, fb, de, ee, rec = await _build_pipeline(pg_pool, ExecutionMode.DRY_RUN)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await de.evaluate(bundle, snapshot_ids, fv_ids)
        if decision and decision.decision_outcome == DecisionOutcome.ALLOW:
            await ee.on_decision_allow(decision)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    reconciled = await rec.reconcile_open_orders()

    async with pg_pool.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM orders")
        fills_count = await conn.fetchval("SELECT COUNT(*) FROM fills")

    assert len(orders) == 1
    assert orders[0]["mode"] == "dry_run"
    assert orders[0]["status"] == "PENDING"
    assert orders[0]["api_response"] is None
    # Reconciler counts open orders in dry_run but writes nothing
    assert reconciled == 1
    assert fills_count == 0
    assert fake._placed_orders == {}
```

- [ ] **Step 2: Run tests (must fail before main wiring)**

Run: `uv run pytest tests/integration/test_pipeline_execution.py -v -m integration`
Expected: 2 PASS (the tests don't depend on main.py wiring — they construct the pipeline directly).

If they FAIL, it indicates a real bug in ExecutionEngine or Reconciler that earlier tests missed — investigate.

- [ ] **Step 3: Update `config/trading.yaml`**

Open `config/trading.yaml`. Find the `trading:` section and add three new keys (keep all existing keys):

```yaml
trading:
  # ... existing keys ...
  execution_mode: dry_run
  min_stake: 2.0
  reconcile_interval: 10
```

- [ ] **Step 4: Wire `main.py`**

Open `src/betfair_trading/main.py`.

Add imports near the existing service imports:

```python
from betfair_trading.models.decision import DecisionOutcome
from betfair_trading.models.order import ExecutionMode
from betfair_trading.services.execution_engine import ExecutionEngine
from betfair_trading.services.reconciler import Reconciler
```

Find the existing snapshot callback registration (currently `on_snapshot_with_decision`). Replace the whole block from `# Initialize decision engine` through `scheduler.set_snapshot_callback(...)` with:

```python
    # Initialize decision engine (Phase 2: real model provider from Phase 2 piece 3)
    decision_engine = DecisionEngine(
        pool=pool,
        provider=provider,
        edge_threshold=trading.get("edge_threshold", 0.02),
        min_liquidity=trading.get("min_liquidity", 100.0),
        max_spread=trading.get("max_spread", 0.10),
        commission_rate=0.05,
        max_positions_per_event=trading.get("max_positions_per_event", 1),
        window_start_minutes=trading.get("window_start_minutes", 120),
        window_end_minutes=trading.get("window_end_minutes", 10),
        daily_dd_max=trading.get("daily_stop_loss_fraction", 0.05),
    )

    # Initialize execution engine + reconciler (Phase 3 baseline)
    execution_mode = ExecutionMode(trading.get("execution_mode", "dry_run"))
    execution_engine = ExecutionEngine(
        pool=pool,
        bf_client=bf_client,
        mode=execution_mode,
        bankroll=trading.get("initial_bankroll", 1000.0),
        kelly_multiplier=trading.get("kelly_fraction", 0.25),
        max_stake_fraction=trading.get("max_stake_fraction", 0.02),
        min_stake=trading.get("min_stake", 2.0),
    )
    reconciler = Reconciler(pool=pool, bf_client=bf_client, mode=execution_mode)

    async def on_snapshot_with_pipeline(bundle, snapshot_ids):
        fv_ids = await feature_builder.on_market_snapshot(bundle, snapshot_ids)
        if not fv_ids:
            return
        decision = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
        if decision is not None and decision.decision_outcome == DecisionOutcome.ALLOW:
            await execution_engine.on_decision_allow(decision)
```

Find the `Scheduler(...)` construction. Add the `reconciler` and `reconcile_interval` kwargs:

```python
    scheduler = Scheduler(
        collector,
        raw_client,
        poll_interval=trading.get("poll_interval", 10),
        discovery_interval=trading.get("discovery_interval", 300),
        reconciler=reconciler,
        reconcile_interval=trading.get("reconcile_interval", 10),
    )
    scheduler.set_snapshot_callback(on_snapshot_with_pipeline)
```

- [ ] **Step 5: Verify imports + syntax**

Run: `uv run python -c "from betfair_trading.main import main"`
Expected: no error.

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 7: Run all tests (no regressions)**

Run: `uv run pytest -v`
Expected: 87 unit + 60 integration = 147 tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/test_pipeline_execution.py src/betfair_trading/main.py config/trading.yaml
git commit -m "feat(main): wire ExecutionEngine + Reconciler into pipeline (dry_run by default)"
```

If ruff applied formatting changes, commit them separately:

```bash
git add -A
git commit -m "chore: ruff format"
```

---

## Task 11: Final verification + push

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

Run: `uv run pytest -v`
Expected: 147 tests pass.

- [ ] **Step 2: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

Run: `uv run ruff format --check src/ tests/`
Expected: idempotent.

- [ ] **Step 3: Update CLAUDE.md**

Modify `CLAUDE.md`. Find the "Currently implemented" section:

```
**Currently implemented (Phases 1-2):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates, Model Inference baseline (LogReg + Platt calibration on Elo+form features).

**Not yet implemented:** Execution Engine, P&L Engine, Kafka messaging.
```

Replace with:

```
**Currently implemented (Phases 1-3a):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates, Model Inference baseline (LogReg + Platt calibration on Elo+form features), Execution Engine baseline (dry_run + paper modes, Kelly sizing, idempotent customerOrderRef, lifecycle-events orders + incremental fills).

**Not yet implemented:** Live order placement, P&L Engine (settlements + daily DD calc), Kafka messaging.
```

- [ ] **Step 4: Commit CLAUDE.md update**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Execution Engine baseline"
```

- [ ] **Step 5: Push**

```bash
git push -u origin feature/execution-engine
```

Capture the GitHub PR URL.

- [ ] **Step 6: Stop here — DO NOT open PR**

DO NOT run `gh pr create`. Report the GitHub URL + final `git log --oneline fb9d236..HEAD` for the user to open the PR manually.

---

## Note finali

- **Live trading**: esplicitamente fuori scope. `ExecutionMode` ha solo `DRY_RUN` e `PAPER`. Aggiungere `LIVE` richiede modifica enum + opt-in nel main + implementazione del wrapper reale su `AsyncBetfairClient`.
- **Settlement reconciliation** (`listClearedOrders`) e P&L Engine: prossimo task Phase 3.
- **Bankroll dinamico**: `_bankroll` in ExecutionEngine è una costante dal config. Aggiornamento dinamico post-settlement arriva con P&L Engine.
- **Reconciliation cadence**: 10s fissa. Cancellation auto su stuck orders è un follow-up.
- **Bug nel codice di produzione durante TDD**: NON fixare in questo plan. Aprire follow-up plan separato.

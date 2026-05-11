# Decision Engine + Risk Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire il Decision Engine che consuma `feature_vectors`, calcola edge per i 3 outcome 1X2, applica 7 risk gate (kill switch, finestra, edge, liquidità, spread, position cap, daily DD), e persiste audit-complete decisions in una nuova tabella `decisions`.

**Architecture:** 3 layer puri (`edge.py`, `gates.py`, `probability_providers.py`) + 1 orchestrator (`decision_engine.py`) + 1 schema migration. Il provider è cablato via Protocol; per Phase 2 due stub (`MarketImpliedProvider`, `BiasedStubProvider`) prendono il posto del Model Inference reale. Il Decision Engine si aggancia al callback del FeatureBuilder via wrapping in `main.py`.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, asyncpg, Alembic, Pydantic v2, testcontainers (integration).

**Reference spec:** `docs/superpowers/specs/2026-05-10-decision-engine-design.md`

---

## File Structure

```
alembic/versions/
└── 002_decisions.py                                # NUOVO - schema migration

src/betfair_trading/
├── models/
│   └── decision.py                                 # NUOVO - Pydantic contract
├── db/
│   └── writer.py                                   # + insert_decision
├── services/
│   ├── edge.py                                     # NUOVO - pure math
│   ├── gates.py                                    # NUOVO - pure predicates
│   ├── probability_providers.py                    # NUOVO - Protocol + 2 stubs
│   └── decision_engine.py                          # NUOVO - orchestrator
└── main.py                                         # + wiring callback

tests/
├── unit/
│   ├── test_edge.py                                # NUOVO
│   ├── test_gates.py                               # NUOVO
│   └── test_probability_providers.py               # NUOVO
└── integration/
    └── test_pipeline_decision.py                   # NUOVO - 7 integration test
```

---

## Pre-requisiti git

L'utente crea il feature branch dal main aggiornato:

```bash
git checkout main
git pull
git checkout -b feature/decision-engine
```

---

## Task 1: Alembic migration `002_decisions.py`

**Files:**
- Create: `alembic/versions/002_decisions.py`

- [ ] **Step 1: Scrivere il file migration**

Create `alembic/versions/002_decisions.py`:

```python
"""decisions table - Phase 2 Decision Engine.

Revision ID: 002
Revises: 001
Create Date: 2026-05-10
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
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
    """)

    op.execute(
        "CREATE INDEX idx_decisions_event ON decisions (event_id, decision_ts);"
    )
    op.execute(
        "CREATE INDEX idx_decisions_market_outcome "
        "ON decisions (market_id, decision_outcome, decision_ts);"
    )
    op.execute(
        "CREATE INDEX idx_decisions_event_allow "
        "ON decisions (event_id) WHERE decision_outcome = 'ALLOW';"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decisions;")
```

- [ ] **Step 2: Verificare che la migrazione applica senza errori**

Run: `uv run pytest tests/integration/test_pg_smoke.py::test_schema_tables_exist -v -m integration`

This test currently asserts the 6 Phase 1 tables exist. With the new migration, `decisions` will also exist, but the test asserts `expected.issubset(found)` so it will still PASS. Verify the migration runs cleanly inside `migrated_db` fixture (no migration error in stderr).

Expected: 1 PASS.

- [ ] **Step 3: Update `test_schema_tables_exist` to include `decisions`**

Modify `tests/integration/test_pg_smoke.py`. Find the `expected` set in `test_schema_tables_exist`:

```python
    expected = {
        "markets",
        "runners",
        "market_snapshots",
        "external_feature_snapshots",
        "feature_vectors",
        "config_snapshots",
    }
```

Add `"decisions"`:

```python
    expected = {
        "markets",
        "runners",
        "market_snapshots",
        "external_feature_snapshots",
        "feature_vectors",
        "config_snapshots",
        "decisions",
    }
```

- [ ] **Step 4: Update `clean_db` fixture to truncate `decisions`**

Modify `tests/integration/conftest.py`. Find the `TRUNCATE` statement in `clean_db`:

```python
    await conn.execute(
        "TRUNCATE markets, runners, market_snapshots, "
        "external_feature_snapshots, feature_vectors, config_snapshots "
        "RESTART IDENTITY CASCADE"
    )
```

Add `decisions`:

```python
    await conn.execute(
        "TRUNCATE markets, runners, market_snapshots, "
        "external_feature_snapshots, feature_vectors, config_snapshots, decisions "
        "RESTART IDENTITY CASCADE"
    )
```

- [ ] **Step 5: Run smoke + verify**

Run: `uv run pytest tests/integration/test_pg_smoke.py -v -m integration`
Expected: 4 PASS.

Run: `uv run pytest -v -m integration`
Expected: tutti i 25 integration test esistenti continuano a passare.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/002_decisions.py tests/integration/test_pg_smoke.py tests/integration/conftest.py
git commit -m "feat(db): add decisions table migration"
```

---

## Task 2: Pydantic Decision contract

**Files:**
- Create: `src/betfair_trading/models/decision.py`
- Create: `tests/unit/test_decision_model.py`

- [ ] **Step 1: Scrivere il test**

Create `tests/unit/test_decision_model.py`:

```python
"""Unit tests for Decision Pydantic contract."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.decision import Decision, DecisionOutcome, GateResult


def test_decision_full_construction():
    fv_id = uuid4()
    snap_id = uuid4()
    cfg_id = uuid4()
    d = Decision(
        market_id="1.A",
        event_id="E-A",
        snapshot_id=snap_id,
        decision_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        model_version="STUB_V1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=101,
        selected_edge_net=Decimal("0.022500"),
        gate_results={
            "kill_switch": GateResult(passed=True, reason="ok"),
            "edge_threshold": GateResult(passed=True, reason="ok"),
        },
        decision_outcome=DecisionOutcome.ALLOW,
        rationale="all gates passed",
        feature_vector_ids=[fv_id],
        config_snapshot_id=cfg_id,
    )
    assert d.decision_outcome == DecisionOutcome.ALLOW
    assert d.selected_runner_id == 101
    assert d.p_model[101] == 0.55
    assert d.gate_results["kill_switch"].passed is True


def test_decision_outcome_enum_values():
    assert DecisionOutcome.ALLOW.value == "ALLOW"
    assert DecisionOutcome.BLOCK_SOFT.value == "BLOCK_SOFT"
    assert DecisionOutcome.BLOCK_HARD.value == "BLOCK_HARD"


def test_gate_result_minimal():
    g = GateResult(passed=False, reason="size_below_min")
    assert g.passed is False
    assert g.reason == "size_below_min"
```

- [ ] **Step 2: Run test (must fail)**

Run: `uv run pytest tests/unit/test_decision_model.py -v -m "not integration"`
Expected: 3 FAIL with `ModuleNotFoundError: No module named 'betfair_trading.models.decision'`.

- [ ] **Step 3: Create the Decision module**

Create `src/betfair_trading/models/decision.py`:

```python
"""Decision Pydantic contract for the Decision Engine."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


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

    selected_runner_id: int | None = None
    selected_edge_net: Decimal | None = None

    gate_results: dict[str, GateResult]
    decision_outcome: DecisionOutcome
    rationale: str | None = None

    feature_vector_ids: list[UUID]
    config_snapshot_id: UUID | None = None
```

- [ ] **Step 4: Run test (must pass)**

Run: `uv run pytest tests/unit/test_decision_model.py -v -m "not integration"`
Expected: 3 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 35 unit tests pass (32 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/models/decision.py tests/unit/test_decision_model.py
git commit -m "feat(models): add Decision Pydantic contract"
```

---

## Task 3: `insert_decision` writer

**Files:**
- Modify: `src/betfair_trading/db/writer.py`
- Create: `tests/integration/test_decision_writer.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_decision_writer.py`:

```python
"""Integration test for insert_decision writer."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_decision
from betfair_trading.models.decision import Decision, DecisionOutcome, GateResult


async def test_insert_decision_persists_all_fields(pg_pool: asyncpg.Pool):
    fv_id = uuid4()
    decision = Decision(
        market_id="1.A",
        event_id="E-A",
        snapshot_id=None,
        decision_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        model_version="STUB_V1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        selected_runner_id=101,
        selected_edge_net=Decimal("0.022500"),
        gate_results={
            "kill_switch": GateResult(passed=True, reason="ok"),
            "edge_threshold": GateResult(passed=True, reason="ok"),
        },
        decision_outcome=DecisionOutcome.ALLOW,
        rationale="all gates passed",
        feature_vector_ids=[fv_id],
        config_snapshot_id=None,
    )

    async with pg_pool.acquire() as conn:
        decision_id = await insert_decision(conn, decision)

    assert decision_id == decision.decision_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decisions WHERE decision_id = $1", decision_id
        )

    assert row is not None
    assert row["market_id"] == "1.A"
    assert row["event_id"] == "E-A"
    assert row["model_version"] == "STUB_V1"
    assert row["selected_runner_id"] == 101
    assert row["selected_edge_net"] == Decimal("0.022500")
    assert row["decision_outcome"] == "ALLOW"
    assert row["rationale"] == "all gates passed"
    assert list(row["feature_vector_ids"]) == [fv_id]

    p_model = row["p_model"]
    if isinstance(p_model, str):
        p_model = json.loads(p_model)
    # JSON keys come back as strings
    assert p_model["101"] == 0.55

    gate_results = row["gate_results"]
    if isinstance(gate_results, str):
        gate_results = json.loads(gate_results)
    assert gate_results["kill_switch"]["passed"] is True
    assert gate_results["kill_switch"]["reason"] == "ok"
```

- [ ] **Step 2: Run test (must fail)**

Run: `uv run pytest tests/integration/test_decision_writer.py -v -m integration`
Expected: 1 FAIL with `ImportError: cannot import name 'insert_decision' from 'betfair_trading.db.writer'`.

- [ ] **Step 3: Add `insert_decision` to writer.py**

Modify `src/betfair_trading/db/writer.py`. Add the import at the top (after the existing `from betfair_trading.models.market import ...` line):

```python
from betfair_trading.models.decision import Decision
```

Add the function at the end of the file:

```python
async def insert_decision(conn: asyncpg.Connection, decision: Decision) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO decisions
           (decision_id, market_id, event_id, snapshot_id, decision_ts,
            model_version, p_model, p_market, edge_gross, edge_net,
            selected_runner_id, selected_edge_net,
            gate_results, decision_outcome, rationale,
            feature_vector_ids, config_snapshot_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
           RETURNING decision_id""",
        decision.decision_id,
        decision.market_id,
        decision.event_id,
        decision.snapshot_id,
        decision.decision_ts,
        decision.model_version,
        json.dumps({str(k): v for k, v in decision.p_model.items()}),
        json.dumps({str(k): v for k, v in decision.p_market.items()}),
        json.dumps({str(k): v for k, v in decision.edge_gross.items()}),
        json.dumps({str(k): v for k, v in decision.edge_net.items()}),
        decision.selected_runner_id,
        decision.selected_edge_net,
        json.dumps(
            {k: {"passed": v.passed, "reason": v.reason}
             for k, v in decision.gate_results.items()}
        ),
        decision.decision_outcome.value,
        decision.rationale,
        decision.feature_vector_ids,
        decision.config_snapshot_id,
    )
```

Note: `feature_vector_ids` is `list[UUID]` and asyncpg sends it as `UUID[]` natively. JSON keys are converted from `int` to `str` because JSON does not allow integer keys.

- [ ] **Step 4: Run test (must pass)**

Run: `uv run pytest tests/integration/test_decision_writer.py -v -m integration`
Expected: 1 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m integration`
Expected: 26 integration tests pass (25 existing + 1 new).

Run: `uv run pytest -v -m "not integration"`
Expected: 35 unit tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/db/writer.py tests/integration/test_decision_writer.py
git commit -m "feat(db): add insert_decision writer with JSONB serialization"
```

---

## Task 4: Pure edge math (`edge.py`)

**Files:**
- Create: `src/betfair_trading/services/edge.py`
- Create: `tests/unit/test_edge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_edge.py`:

```python
"""Unit tests for pure edge math functions."""

import math

from betfair_trading.services.edge import compute_market_probs, compute_net_edge


def test_market_probs_normalize_to_one():
    quotes = {101: 2.0, 102: 4.0, 103: 4.0}  # implied: 0.5, 0.25, 0.25
    probs = compute_market_probs(quotes)
    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert math.isclose(probs[101], 0.5, abs_tol=1e-9)
    assert math.isclose(probs[102], 0.25, abs_tol=1e-9)
    assert math.isclose(probs[103], 0.25, abs_tol=1e-9)


def test_market_probs_handles_overround():
    """Real markets have overround: sum of 1/odds > 1.0. Normalization fixes it."""
    quotes = {101: 1.9, 102: 3.5, 103: 4.0}  # implied sum > 1
    probs = compute_market_probs(quotes)
    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_market_probs_skips_missing_quote():
    quotes = {101: 2.0, 102: None, 103: 4.0}
    probs = compute_market_probs(quotes)
    assert probs[102] == 0.0
    # Sum across the two with valid quotes should be 1.0
    assert math.isclose(probs[101] + probs[103], 1.0, abs_tol=1e-9)


def test_market_probs_skips_zero_or_negative():
    quotes = {101: 2.0, 102: 0.0, 103: -1.0}
    probs = compute_market_probs(quotes)
    assert probs[101] == 1.0
    assert probs[102] == 0.0
    assert probs[103] == 0.0


def test_net_edge_with_zero_commission():
    gross, net = compute_net_edge(p_model=0.55, p_market=0.50, commission_rate=0.0)
    assert math.isclose(gross, 0.05, abs_tol=1e-9)
    assert math.isclose(net, 0.05, abs_tol=1e-9)


def test_net_edge_with_default_commission():
    gross, net = compute_net_edge(p_model=0.55, p_market=0.50, commission_rate=0.05)
    assert math.isclose(gross, 0.05, abs_tol=1e-9)
    # net = 0.55 * 0.95 - 0.50 = 0.5225 - 0.50 = 0.0225
    assert math.isclose(net, 0.0225, abs_tol=1e-9)


def test_net_edge_negative_when_p_model_below_market():
    gross, net = compute_net_edge(p_model=0.40, p_market=0.50, commission_rate=0.05)
    assert gross < 0
    assert net < 0
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_edge.py -v -m "not integration"`
Expected: 7 FAIL with `ModuleNotFoundError: No module named 'betfair_trading.services.edge'`.

- [ ] **Step 3: Create `edge.py`**

Create `src/betfair_trading/services/edge.py`:

```python
"""Pure edge math: market-implied probabilities and net edge calculation."""


def compute_market_probs(runner_quotes: dict[int, float | None]) -> dict[int, float]:
    """Normalize 1/odds_i across runners with valid quotes.

    Args:
        runner_quotes: {runner_id: best_back_price}.

    Returns:
        {runner_id: market-implied probability}. Sum across runners with valid
        positive quotes equals 1.0; runners with None or non-positive prices
        get prob=0.0.
    """
    raw = {}
    for rid, price in runner_quotes.items():
        if price is None or price <= 0:
            raw[rid] = 0.0
        else:
            raw[rid] = 1.0 / price

    total = sum(raw.values())
    if total <= 0:
        return {rid: 0.0 for rid in runner_quotes}

    return {rid: p / total for rid, p in raw.items()}


def compute_net_edge(
    p_model: float, p_market: float, commission_rate: float = 0.05
) -> tuple[float, float]:
    """Compute (edge_gross, edge_net) for a single outcome.

    edge_gross = p_model - p_market
    edge_net   = p_model * (1 - commission_rate) - p_market

    Args:
        p_model: model probability of this outcome winning.
        p_market: market-implied probability of this outcome winning.
        commission_rate: fractional commission on winnings (Betfair default 0.05).

    Returns:
        (edge_gross, edge_net) as floats.
    """
    edge_gross = p_model - p_market
    edge_net = p_model * (1.0 - commission_rate) - p_market
    return edge_gross, edge_net
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_edge.py -v -m "not integration"`
Expected: 7 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 42 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/edge.py tests/unit/test_edge.py
git commit -m "feat(decision): add pure edge math (compute_market_probs, compute_net_edge)"
```

---

## Task 5: Pure gate predicates (`gates.py`)

**Files:**
- Create: `src/betfair_trading/services/gates.py`
- Create: `tests/unit/test_gates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_gates.py`:

```python
"""Unit tests for risk gate predicates."""

from decimal import Decimal

from betfair_trading.services.gates import (
    check_daily_drawdown,
    check_edge_threshold,
    check_kill_switch,
    check_liquidity,
    check_position_limit,
    check_spread,
    check_window,
)


def test_check_kill_switch_inactive_passes():
    passed, reason = check_kill_switch(active=False)
    assert passed is True
    assert reason == "ok"


def test_check_kill_switch_active_fails():
    passed, reason = check_kill_switch(active=True)
    assert passed is False
    assert reason == "kill_switch_active"


def test_check_window_in_range_passes():
    passed, reason = check_window(minutes_to_start=60.0, window_start_min=120, window_end_min=10)
    assert passed is True


def test_check_window_too_far_fails():
    passed, reason = check_window(minutes_to_start=200.0, window_start_min=120, window_end_min=10)
    assert passed is False
    assert "too_far" in reason or "outside" in reason


def test_check_window_too_close_fails():
    passed, reason = check_window(minutes_to_start=5.0, window_start_min=120, window_end_min=10)
    assert passed is False


def test_check_edge_threshold_above_passes():
    passed, _ = check_edge_threshold(edge_net=0.025, threshold=0.02)
    assert passed is True


def test_check_edge_threshold_below_fails():
    passed, reason = check_edge_threshold(edge_net=0.01, threshold=0.02)
    assert passed is False
    assert "below" in reason


def test_check_liquidity_above_passes():
    passed, _ = check_liquidity(best_back_size=Decimal("200"), min_liquidity=100.0)
    assert passed is True


def test_check_liquidity_below_fails():
    passed, reason = check_liquidity(best_back_size=Decimal("50"), min_liquidity=100.0)
    assert passed is False


def test_check_liquidity_none_fails():
    passed, _ = check_liquidity(best_back_size=None, min_liquidity=100.0)
    assert passed is False


def test_check_spread_below_passes():
    passed, _ = check_spread(spread=Decimal("0.04"), max_spread=0.10)
    assert passed is True


def test_check_spread_above_fails():
    passed, reason = check_spread(spread=Decimal("0.50"), max_spread=0.10)
    assert passed is False


def test_check_spread_none_fails():
    passed, _ = check_spread(spread=None, max_spread=0.10)
    assert passed is False


def test_check_position_limit_under_cap_passes():
    passed, _ = check_position_limit(allow_count=0, max_per_event=1)
    assert passed is True


def test_check_position_limit_at_cap_fails():
    passed, reason = check_position_limit(allow_count=1, max_per_event=1)
    assert passed is False


def test_check_daily_drawdown_under_limit_passes():
    passed, _ = check_daily_drawdown(current_dd_fraction=0.02, max_dd_fraction=0.05)
    assert passed is True


def test_check_daily_drawdown_at_limit_fails():
    passed, reason = check_daily_drawdown(current_dd_fraction=0.05, max_dd_fraction=0.05)
    assert passed is False
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_gates.py -v -m "not integration"`
Expected: 17 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `gates.py`**

Create `src/betfair_trading/services/gates.py`:

```python
"""Pure risk-gate predicates for the Decision Engine.

Each gate returns (passed: bool, reason: str). Reason is "ok" on pass,
descriptive on fail.
"""

from decimal import Decimal


def check_kill_switch(active: bool) -> tuple[bool, str]:
    if active:
        return False, "kill_switch_active"
    return True, "ok"


def check_window(
    minutes_to_start: float, window_start_min: int, window_end_min: int
) -> tuple[bool, str]:
    """Pass when minutes_to_start ∈ [window_end_min, window_start_min]."""
    if minutes_to_start > window_start_min:
        return False, "outside_window_too_far"
    if minutes_to_start < window_end_min:
        return False, "outside_window_too_close"
    return True, "ok"


def check_edge_threshold(edge_net: float, threshold: float) -> tuple[bool, str]:
    if edge_net < threshold:
        return False, "edge_below_threshold"
    return True, "ok"


def check_liquidity(
    best_back_size: Decimal | None, min_liquidity: float
) -> tuple[bool, str]:
    if best_back_size is None:
        return False, "size_missing"
    if float(best_back_size) < min_liquidity:
        return False, "size_below_min"
    return True, "ok"


def check_spread(spread: Decimal | None, max_spread: float) -> tuple[bool, str]:
    if spread is None:
        return False, "spread_missing"
    if float(spread) > max_spread:
        return False, "spread_above_max"
    return True, "ok"


def check_position_limit(allow_count: int, max_per_event: int) -> tuple[bool, str]:
    if allow_count >= max_per_event:
        return False, "position_limit_reached"
    return True, "ok"


def check_daily_drawdown(
    current_dd_fraction: float, max_dd_fraction: float
) -> tuple[bool, str]:
    """Phase 2: current_dd_fraction is hardcoded 0.0 by callers until P&L exists."""
    if current_dd_fraction >= max_dd_fraction:
        return False, "daily_drawdown_reached"
    return True, "ok"
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_gates.py -v -m "not integration"`
Expected: 17 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 59 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/gates.py tests/unit/test_gates.py
git commit -m "feat(decision): add pure risk gate predicates"
```

---

## Task 6: ProbabilityProvider Protocol + 2 stub providers

**Files:**
- Create: `src/betfair_trading/services/probability_providers.py`
- Create: `tests/unit/test_probability_providers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_probability_providers.py`:

```python
"""Unit tests for ProbabilityProvider stub implementations."""

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from betfair_trading.models.market import (
    MarketSnapshotBundle,
    Runner,
    RunnerSnapshot,
)
from betfair_trading.services.probability_providers import (
    BiasedStubProvider,
    MarketImpliedProvider,
)


def _make_bundle_and_runners():
    bundle = MarketSnapshotBundle(
        market_id="1.A", event_id="E-A",
        snapshot_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        runners=[
            RunnerSnapshot(runner_id=101, best_back_price=Decimal("2.0"),
                           best_lay_price=Decimal("2.04"), traded_volume=Decimal("0")),
            RunnerSnapshot(runner_id=102, best_back_price=Decimal("3.5"),
                           best_lay_price=Decimal("3.6"), traded_volume=Decimal("0")),
            RunnerSnapshot(runner_id=103, best_back_price=Decimal("4.0"),
                           best_lay_price=Decimal("4.1"), traded_volume=Decimal("0")),
        ],
        market_status="OPEN", inplay=False, total_matched=Decimal("1000"),
        minutes_to_start=60.0,
    )
    runners = [
        Runner(runner_id=101, runner_name="Liverpool", sort_priority=1),
        Runner(runner_id=102, runner_name="The Draw", sort_priority=2),
        Runner(runner_id=103, runner_name="Arsenal", sort_priority=3),
    ]
    return bundle, runners


@pytest.mark.asyncio
async def test_market_implied_provider_returns_normalized_probs():
    bundle, runners = _make_bundle_and_runners()
    provider = MarketImpliedProvider()
    probs = await provider.get_probabilities(bundle, runners, feature_vector_ids=[])

    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    # Home (odds 2.0) should have largest implied prob
    assert probs[101] > probs[102]
    assert probs[101] > probs[103]


@pytest.mark.asyncio
async def test_market_implied_provider_version():
    provider = MarketImpliedProvider()
    assert provider.model_version == "STUB_MARKET_IMPLIED_V1"


@pytest.mark.asyncio
async def test_biased_stub_provider_shifts_home():
    bundle, runners = _make_bundle_and_runners()
    market_provider = MarketImpliedProvider()
    market_probs = await market_provider.get_probabilities(bundle, runners, [])

    biased_provider = BiasedStubProvider(home_bias=0.05)
    biased_probs = await biased_provider.get_probabilities(bundle, runners, [])

    # Home should get more weight after bias
    assert biased_probs[101] > market_probs[101]
    # Sum still equals 1.0
    total = sum(biased_probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_biased_stub_provider_version():
    provider = BiasedStubProvider(home_bias=0.05)
    assert provider.model_version == "STUB_BIAS_V1"
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_probability_providers.py -v -m "not integration"`
Expected: 4 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `probability_providers.py`**

Create `src/betfair_trading/services/probability_providers.py`:

```python
"""ProbabilityProvider Protocol + stub implementations for Phase 2.

The real Model Inference will provide a third implementation in Phase 3.
"""

import uuid
from typing import Protocol

from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.edge import compute_market_probs


class ProbabilityProvider(Protocol):
    """Source of model probabilities for outcomes (home/draw/away)."""

    @property
    def model_version(self) -> str: ...

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]: ...


def _runner_quotes(bundle: MarketSnapshotBundle, runners: list[Runner]) -> dict[int, float | None]:
    """Map runner_id → best_back_price (float or None) using the bundle's snapshot."""
    bundle_by_id = {rs.runner_id: rs for rs in bundle.runners}
    out: dict[int, float | None] = {}
    for r in runners:
        snap = bundle_by_id.get(r.runner_id)
        if snap is None or snap.best_back_price is None:
            out[r.runner_id] = None
        else:
            out[r.runner_id] = float(snap.best_back_price)
    return out


class MarketImpliedProvider:
    """Returns the market-implied probabilities (no edge by construction).

    Useful for sanity tests: every outcome's edge_gross is exactly 0.
    """

    model_version = "STUB_MARKET_IMPLIED_V1"

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]:
        return compute_market_probs(_runner_quotes(bundle, runners))


class BiasedStubProvider:
    """Market-implied + bias on the home runner (sort_priority=1).

    `home_bias` is added to the home prob; the same total is subtracted
    proportionally from the other runners. Result is renormalized to sum to 1.0.
    """

    model_version = "STUB_BIAS_V1"

    def __init__(self, home_bias: float = 0.05):
        self.home_bias = home_bias

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]:
        market_probs = compute_market_probs(_runner_quotes(bundle, runners))

        # Identify home runner: smallest non-None sort_priority
        sorted_runners = sorted(
            runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
        )
        if not sorted_runners:
            return market_probs
        home_id = sorted_runners[0].runner_id

        n_others = max(1, len(market_probs) - 1)
        biased: dict[int, float] = {}
        for rid, p in market_probs.items():
            if rid == home_id:
                biased[rid] = p + self.home_bias
            else:
                biased[rid] = max(0.0, p - self.home_bias / n_others)

        total = sum(biased.values())
        if total <= 0:
            return market_probs
        return {rid: p / total for rid, p in biased.items()}
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_probability_providers.py -v -m "not integration"`
Expected: 4 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 63 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/probability_providers.py tests/unit/test_probability_providers.py
git commit -m "feat(decision): add ProbabilityProvider Protocol + market-implied/biased stubs"
```

---

## Task 7: Decision Engine orchestrator

**Files:**
- Create: `src/betfair_trading/services/decision_engine.py`
- Create: `tests/integration/test_pipeline_decision.py`

- [ ] **Step 1: Write the 7 failing integration tests**

Create `tests/integration/test_pipeline_decision.py`:

```python
"""End-to-end Decision Engine tests."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from betfair_trading.db.writer import insert_config_snapshot
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.probability_providers import (
    BiasedStubProvider,
    MarketImpliedProvider,
)
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


def _make_engine(pg_pool, provider, **overrides):
    defaults = dict(
        pool=pg_pool,
        provider=provider,
        edge_threshold=0.02,
        min_liquidity=100.0,
        max_spread=0.10,
        commission_rate=0.05,
        max_positions_per_event=1,
        window_start_minutes=120,
        window_end_minutes=10,
        daily_dd_max=0.05,
    )
    defaults.update(overrides)
    return DecisionEngine(**defaults)


async def _run_pipeline(pg_pool, fake, decision_engine, fb=None):
    """Run discovery + 1 poll cycle wired through fb + decision_engine."""
    if fb is None:
        fb = FeatureBuilder(pg_pool, external_ingestor=None)
    collector = MarketCollector(
        fake, pg_pool, window_start_minutes=120, window_end_minutes=10
    )

    decisions_made = []

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            did = await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)
            decisions_made.append(did)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    return decisions_made


async def test_allow_path_with_biased_provider(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal",
                                start_time=datetime.now(UTC) + timedelta(minutes=60)))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_outcome, selected_runner_id "
            "FROM decisions WHERE market_id = '1.A'"
        )
    assert len(rows) == 1
    assert rows[0]["decision_outcome"] == "ALLOW"
    assert rows[0]["selected_runner_id"] == 101  # home runner_id from make_market default


async def test_block_soft_when_market_implied_provider(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    engine = _make_engine(pg_pool, MarketImpliedProvider())
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["edge_threshold"]["passed"] is False


async def test_block_soft_low_liquidity(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    # Sizes well below min_liquidity=100
    fake.queue_book("1.A", make_book(
        market_id="1.A",
        runner_quotes=[(101, 2.0, 2.04, 50.0, 50.0),
                       (102, 3.5, 3.6, 50.0, 50.0),
                       (103, 4.0, 4.1, 50.0, 50.0)],
    ))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["liquidity"]["passed"] is False


async def test_block_soft_high_spread(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    # Spread = lay - back = 2.50 - 2.0 = 0.50, above max_spread=0.10
    fake.queue_book("1.A", make_book(
        market_id="1.A",
        runner_quotes=[(101, 2.0, 2.50, 500.0, 500.0),
                       (102, 3.5, 3.6, 500.0, 500.0),
                       (103, 4.0, 4.1, 500.0, 500.0)],
    ))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_SOFT"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["spread"]["passed"] is False


async def test_block_hard_kill_switch(pg_pool: asyncpg.Pool):
    # Insert config_snapshot with kill_switch_active=True
    async with pg_pool.acquire() as conn:
        await insert_config_snapshot(conn, {"trading": {}}, kill_switch_active=True)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_outcome, gate_results FROM decisions WHERE market_id='1.A'"
        )
    assert row["decision_outcome"] == "BLOCK_HARD"
    gate = row["gate_results"]
    if isinstance(gate, str):
        gate = json.loads(gate)
    assert gate["kill_switch"]["passed"] is False


async def test_position_limit_blocks_second_allow(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A"))
    # Two books queued → two poll cycles
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            await engine.evaluate(bundle, snapshot_ids, fv_ids)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)
    await collector.run_poll_cycle(on_snapshot=on_snap)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_outcome FROM decisions "
            "WHERE event_id = 'E-A' ORDER BY decision_ts"
        )
    assert len(rows) == 2
    assert rows[0]["decision_outcome"] == "ALLOW"
    assert rows[1]["decision_outcome"] == "BLOCK_SOFT"


async def test_decision_persists_full_audit(pg_pool: asyncpg.Pool):
    # Seed config_snapshot so config_snapshot_id is non-NULL
    async with pg_pool.acquire() as conn:
        cfg_id = await insert_config_snapshot(
            conn, {"trading": {"edge_threshold": 0.02}}, kill_switch_active=False
        )

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    engine = _make_engine(pg_pool, BiasedStubProvider(home_bias=0.05))
    await _run_pipeline(pg_pool, fake, engine)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT p_model, p_market, edge_gross, edge_net, gate_results, "
            "feature_vector_ids, config_snapshot_id, model_version "
            "FROM decisions WHERE market_id = '1.A'"
        )

    p_model = row["p_model"] if not isinstance(row["p_model"], str) else json.loads(row["p_model"])
    p_market = row["p_market"] if not isinstance(row["p_market"], str) else json.loads(row["p_market"])
    edge_gross = row["edge_gross"] if not isinstance(row["edge_gross"], str) else json.loads(row["edge_gross"])
    edge_net = row["edge_net"] if not isinstance(row["edge_net"], str) else json.loads(row["edge_net"])
    gate = row["gate_results"] if not isinstance(row["gate_results"], str) else json.loads(row["gate_results"])

    # 3 outcomes
    assert len(p_model) == 3
    assert len(p_market) == 3
    assert len(edge_gross) == 3
    assert len(edge_net) == 3

    # 7 gates
    assert set(gate.keys()) == {
        "kill_switch", "window", "edge_threshold",
        "liquidity", "spread", "position_limit", "daily_drawdown",
    }

    # Audit linkage
    assert len(row["feature_vector_ids"]) > 0
    assert row["config_snapshot_id"] == cfg_id
    assert row["model_version"] == "STUB_BIAS_V1"
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_pipeline_decision.py -v -m integration`
Expected: 7 FAIL with `ModuleNotFoundError: No module named 'betfair_trading.services.decision_engine'`.

- [ ] **Step 3: Create `decision_engine.py`**

Create `src/betfair_trading/services/decision_engine.py`:

```python
"""Decision Engine: consumes feature_vectors, computes per-outcome edge,
applies risk gates, persists audit-complete decisions."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import structlog

from betfair_trading.db.writer import insert_decision
from betfair_trading.models.decision import (
    Decision,
    DecisionOutcome,
    GateResult,
)
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.edge import compute_market_probs, compute_net_edge
from betfair_trading.services.gates import (
    check_daily_drawdown,
    check_edge_threshold,
    check_kill_switch,
    check_liquidity,
    check_position_limit,
    check_spread,
    check_window,
)
from betfair_trading.services.probability_providers import ProbabilityProvider

log = structlog.get_logger()


class DecisionEngine:
    def __init__(
        self,
        pool: asyncpg.Pool,
        provider: ProbabilityProvider,
        edge_threshold: float = 0.02,
        min_liquidity: float = 100.0,
        max_spread: float = 0.10,
        commission_rate: float = 0.05,
        max_positions_per_event: int = 1,
        window_start_minutes: int = 120,
        window_end_minutes: int = 10,
        daily_dd_max: float = 0.05,
    ):
        self._pool = pool
        self._provider = provider
        self._edge_threshold = edge_threshold
        self._min_liquidity = min_liquidity
        self._max_spread = max_spread
        self._commission_rate = commission_rate
        self._max_positions_per_event = max_positions_per_event
        self._window_start_minutes = window_start_minutes
        self._window_end_minutes = window_end_minutes
        self._daily_dd_max = daily_dd_max
        self._runner_meta_cache: dict[str, list[Runner]] = {}

    async def evaluate(
        self,
        bundle: MarketSnapshotBundle,
        snapshot_ids: list[uuid.UUID],
        feature_vector_ids: list[uuid.UUID],
    ) -> uuid.UUID | None:
        async with self._pool.acquire() as conn:
            runners_meta = await self._load_runners(conn, bundle.market_id)
            if not runners_meta:
                log.warning("decision_skip_no_runner_meta", market_id=bundle.market_id)
                return None

            cfg_row = await conn.fetchrow(
                "SELECT config_snapshot_id, kill_switch_active "
                "FROM config_snapshots ORDER BY effective_ts DESC LIMIT 1"
            )
            kill_switch_active = bool(cfg_row["kill_switch_active"]) if cfg_row else False
            config_snapshot_id = cfg_row["config_snapshot_id"] if cfg_row else None

            # Index bundle runners by id
            bundle_by_id = {rs.runner_id: rs for rs in bundle.runners}

            runner_quotes: dict[int, float | None] = {
                r.runner_id: (
                    float(bundle_by_id[r.runner_id].best_back_price)
                    if (
                        r.runner_id in bundle_by_id
                        and bundle_by_id[r.runner_id].best_back_price is not None
                    )
                    else None
                )
                for r in runners_meta
            }
            p_market = compute_market_probs(runner_quotes)

            p_model = await self._provider.get_probabilities(
                bundle, runners_meta, feature_vector_ids
            )

            edge_gross: dict[int, float] = {}
            edge_net: dict[int, float] = {}
            for r in runners_meta:
                gross, net = compute_net_edge(
                    p_model.get(r.runner_id, 0.0),
                    p_market.get(r.runner_id, 0.0),
                    self._commission_rate,
                )
                edge_gross[r.runner_id] = gross
                edge_net[r.runner_id] = net

            selected_runner_id = max(edge_net, key=lambda rid: edge_net[rid])
            selected_edge_net = edge_net[selected_runner_id]
            selected_runner_snapshot = bundle_by_id.get(selected_runner_id)

            allow_count = await conn.fetchval(
                "SELECT COUNT(*) FROM decisions "
                "WHERE event_id = $1 AND decision_outcome = 'ALLOW'",
                bundle.event_id,
            )

            def _gr(result: tuple[bool, str]) -> GateResult:
                return GateResult(passed=result[0], reason=result[1])

            best_back_size = (
                selected_runner_snapshot.best_back_size if selected_runner_snapshot else None
            )
            best_spread = (
                selected_runner_snapshot.spread if selected_runner_snapshot else None
            )

            gate_results: dict[str, GateResult] = {
                "kill_switch": _gr(check_kill_switch(kill_switch_active)),
                "window": _gr(check_window(
                    bundle.minutes_to_start,
                    self._window_start_minutes,
                    self._window_end_minutes,
                )),
                "edge_threshold": _gr(
                    check_edge_threshold(selected_edge_net, self._edge_threshold)
                ),
                "liquidity": _gr(check_liquidity(best_back_size, self._min_liquidity)),
                "spread": _gr(check_spread(best_spread, self._max_spread)),
                "position_limit": _gr(
                    check_position_limit(allow_count, self._max_positions_per_event)
                ),
                "daily_drawdown": _gr(check_daily_drawdown(0.0, self._daily_dd_max)),
            }

            outcome = self._determine_outcome(gate_results)
            rationale = self._build_rationale(gate_results, outcome)
            snapshot_id = snapshot_ids[0] if snapshot_ids else None

            decision = Decision(
                market_id=bundle.market_id,
                event_id=bundle.event_id,
                snapshot_id=snapshot_id,
                decision_ts=datetime.now(UTC),
                model_version=self._provider.model_version,
                p_model=p_model,
                p_market=p_market,
                edge_gross=edge_gross,
                edge_net=edge_net,
                selected_runner_id=selected_runner_id,
                selected_edge_net=Decimal(str(round(selected_edge_net, 6))),
                gate_results=gate_results,
                decision_outcome=outcome,
                rationale=rationale,
                feature_vector_ids=feature_vector_ids,
                config_snapshot_id=config_snapshot_id,
            )
            decision_id = await insert_decision(conn, decision)

        log.info(
            "decision_made",
            market_id=bundle.market_id,
            outcome=outcome.value,
            selected_runner=selected_runner_id,
            edge_net=selected_edge_net,
        )
        return decision_id

    @staticmethod
    def _determine_outcome(gate_results: dict[str, GateResult]) -> DecisionOutcome:
        if not gate_results["kill_switch"].passed:
            return DecisionOutcome.BLOCK_HARD
        if any(not r.passed for r in gate_results.values()):
            return DecisionOutcome.BLOCK_SOFT
        return DecisionOutcome.ALLOW

    @staticmethod
    def _build_rationale(
        gate_results: dict[str, GateResult], outcome: DecisionOutcome
    ) -> str:
        if outcome == DecisionOutcome.ALLOW:
            return "all gates passed"
        failed = [
            f"{name}:{r.reason}" for name, r in gate_results.items() if not r.passed
        ]
        return "; ".join(failed)

    async def _load_runners(
        self, conn: asyncpg.Connection, market_id: str
    ) -> list[Runner]:
        if market_id in self._runner_meta_cache:
            return self._runner_meta_cache[market_id]
        rows = await conn.fetch(
            "SELECT runner_id, runner_name, sort_priority FROM runners "
            "WHERE market_id = $1 ORDER BY sort_priority NULLS LAST, runner_id",
            market_id,
        )
        runners = [
            Runner(
                runner_id=r["runner_id"],
                runner_name=r["runner_name"],
                sort_priority=r["sort_priority"],
            )
            for r in rows
        ]
        if runners:
            self._runner_meta_cache[market_id] = runners
        return runners
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_pipeline_decision.py -v -m integration`
Expected: 7 PASS.

If a test fails:
- For `test_block_hard_kill_switch`: `clean_db` truncates `config_snapshots`, so the kill-switch row is deleted between tests. Insert it inside the test BEFORE running the pipeline. The test does this via `insert_config_snapshot(...)`. Verify that the order is: `INSERT config_snapshots → run pipeline → assert`.
- For `test_position_limit_blocks_second_allow`: requires `idx_decisions_event_allow` to be effective. Verify the index was created in the migration.

Do NOT modify production code blindly to make tests pass. Read errors, report DONE_WITH_CONCERNS if a real bug emerges.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 63 unit tests pass.

Run: `uv run pytest -v -m integration`
Expected: 33 integration tests pass (26 from prior + 7 new).

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/betfair_trading/services/decision_engine.py tests/integration/test_pipeline_decision.py
git commit -m "feat(decision): add DecisionEngine orchestrator with 7 risk gates"
```

If ruff applied formatting changes, commit them separately:

```bash
git add -A
git commit -m "chore: ruff format"
```

---

## Task 8: Wire DecisionEngine into `main.py`

**Files:**
- Modify: `src/betfair_trading/main.py`

- [ ] **Step 1: Update imports**

Open `src/betfair_trading/main.py`. Add the imports near the existing service imports:

```python
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.probability_providers import BiasedStubProvider
```

- [ ] **Step 2: Instantiate provider + engine and wrap callback**

After the existing `feature_builder = FeatureBuilder(pool, ingestor)` line, add the Decision Engine setup. Replace:

```python
    # Initialize feature builder
    feature_builder = FeatureBuilder(pool, ingestor)
```

(or whatever the exact line is) with the same line plus:

```python
    # Initialize feature builder
    feature_builder = FeatureBuilder(pool, ingestor)

    # Initialize decision engine (Phase 2: stub provider)
    provider = BiasedStubProvider(home_bias=0.05)
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
```

Then update the snapshot callback registration. Find where it currently does:

```python
    scheduler.set_snapshot_callback(feature_builder.on_market_snapshot)
```

Replace with:

```python
    async def on_snapshot_with_decision(bundle, snapshot_ids):
        fv_ids = await feature_builder.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            await decision_engine.evaluate(bundle, snapshot_ids, fv_ids)

    scheduler.set_snapshot_callback(on_snapshot_with_decision)
```

- [ ] **Step 3: Verify imports + syntax**

Run: `uv run python -c "from betfair_trading.main import main"`
Expected: no error (the function is defined and imports resolve).

- [ ] **Step 4: Lint**

Run: `uv run ruff check src/betfair_trading/main.py`
Expected: clean.

- [ ] **Step 5: Run all tests (no regressions)**

Run: `uv run pytest -v`
Expected: 63 unit + 33 integration = 96 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/main.py
git commit -m "feat(main): wire DecisionEngine into snapshot callback"
```

---

## Task 9: Final verification + push

**Files:** none modified — only verification.

- [ ] **Step 1: Full suite**

Run: `uv run pytest -v`
Expected: 96 tests PASS, integration suite under 10s.

- [ ] **Step 2: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

Run: `uv run ruff format --check src/ tests/`
Expected: idempotent (no formatting changes needed).

- [ ] **Step 3: Update CLAUDE.md to reflect the new state**

Modify `CLAUDE.md`. Find the section "Currently implemented (Phase 1):" and update:

Replace:
```
**Currently implemented (Phase 1):** Market Data Collector, External Data Ingestor, Feature Builder (A0 baseline), Scheduler, DB audit layer.

**Not yet implemented:** Model Inference, Decision Engine, Execution Engine, P&L Engine, Kill Switch enforcement, Kafka messaging.
```

with:

```
**Currently implemented (Phases 1-2):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates (with stub probability provider).

**Not yet implemented:** Model Inference (real supervised), Execution Engine, P&L Engine, Kafka messaging.
```

- [ ] **Step 4: Commit CLAUDE.md update**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect Phase 2 Decision Engine"
```

- [ ] **Step 5: Push the feature branch**

```bash
git push -u origin feature/decision-engine
```

Capture the GitHub PR URL printed by the push command.

- [ ] **Step 6: Stop here**

DO NOT open the PR via `gh` (likely not authenticated). Report the GitHub URL hint and the final `git log --oneline ff07b6b..HEAD` output (or whatever the previous merge commit is) so the user can review and open the PR manually.

---

## Note finali

- **`bankroll_snapshots` + daily DD reale**: il gate `check_daily_drawdown` è stub PASS-through finché P&L Engine non esiste. Quando arriverà, sostituiremo `0.0` con il valore corrente.
- **`model_inferences` table**: non in questo plan. La probability source live in `decisions.p_model` con `model_version` come traccia. Quando il vero Model Inference arriverà, aggiungeremo la tabella e linkeremo via `inference_id`.
- **Eviction cache**: `_runner_meta_cache` cresce O(N markets attivi). Per Phase 2 (~100 markets) trascurabile. Eviction è follow-up.
- **Bug nel codice di produzione scoperti durante TDD**: NON fixare in questo plan. Aprire follow-up plan separato.

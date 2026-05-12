# Model Inference Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sostituire il `BiasedStubProvider` con un vero `ModelInferenceProvider` (LogisticRegression calibrato con Platt) che predice probabilità su Elo+form features, persiste `model_inferences`, e linka via `decisions.inference_id`. Include il CLI training su CSV storico.

**Architecture:** Nuovo sotto-package `training/` (features + dataset + CLI) + nuovo file in `services/` (provider). Schema arricchito con `model_versions`, `model_inferences`, e una colonna `inference_id` su `decisions`. Il `ProbabilityProvider` Protocol cambia signature (ritorna tuple `(probs, inference_id)`), i 2 stub esistenti vengono aggiornati. Zero skew train/serve garantito da `build_feature_dict()` shared.

**Tech Stack:** Python 3.12, scikit-learn 1.5+, numpy, joblib, pytest, asyncpg, Alembic, Pydantic v2.

**Reference spec:** `docs/superpowers/specs/2026-05-11-model-inference-design.md`

---

## File Structure

```
alembic/versions/
└── 003_model_versions_inferences.py        # NUOVO - schema migration

models/                                      # NUOVA directory
└── .gitkeep                                 # placeholder

src/betfair_trading/
├── training/                                # NUOVO sotto-package
│   ├── __init__.py                          # empty
│   ├── features.py                          # FEATURE_NAMES + build_feature_dict
│   ├── dataset.py                           # DatasetBuilder (CSV replay)
│   └── train.py                             # CLI: fit + calibrate + save + DB insert
├── models/
│   ├── inference.py                         # NUOVO - ModelVersion, ModelInference
│   └── decision.py                          # + inference_id field
├── db/
│   └── writer.py                            # + insert_model_{version,inference}; update insert_decision
├── services/
│   ├── probability_providers.py             # Protocol breaking change + stubs update
│   ├── decision_engine.py                   # unpack tuple, set inference_id
│   └── model_inference_provider.py          # NUOVO - real provider
└── main.py                                  # swap BiasedStub for ModelInferenceProvider

tests/
├── unit/
│   ├── test_decision_model.py               # + test for inference_id field
│   ├── test_probability_providers.py        # update return-shape assertions
│   ├── test_training_features.py            # NUOVO - 5 test
│   └── test_dataset_builder.py              # NUOVO - 3 test
└── integration/
    ├── conftest.py                          # TRUNCATE includes new tables
    ├── test_pg_smoke.py                     # + new tables in expected set
    ├── test_decision_writer.py              # update for inference_id round-trip
    ├── test_pipeline_decision.py            # update for tuple unpack
    ├── test_model_inference_provider.py     # NUOVO - 5 test
    └── test_train_cli.py                    # NUOVO - 2 test

pyproject.toml                                # + scikit-learn, numpy, joblib
.gitignore                                    # + models/*.joblib
```

---

## Pre-requisiti git

L'utente crea il feature branch:

```bash
git checkout main
git pull
git checkout -b feature/model-inference
```

---

## Task 1: Schema migration `003_model_versions_inferences.py` + fixture updates

**Files:**
- Create: `alembic/versions/003_model_versions_inferences.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_pg_smoke.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/003_model_versions_inferences.py`:

```python
"""model_versions + model_inferences tables; decisions.inference_id column.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE model_versions (
        model_version_id     UUID         NOT NULL DEFAULT uuid_generate_v4(),
        model_name           TEXT         NOT NULL,
        feature_set_version  TEXT         NOT NULL,
        created_ts           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        file_path            TEXT         NOT NULL,
        training_data_hash   TEXT         NOT NULL,
        training_csv_path    TEXT         NOT NULL,
        training_params      JSONB        NOT NULL DEFAULT '{}',
        metrics              JSONB        NOT NULL DEFAULT '{}',
        feature_names        JSONB        NOT NULL,
        n_train              INT          NOT NULL,
        n_test               INT          NOT NULL,
        PRIMARY KEY (model_version_id)
    );
    """)
    op.execute(
        "CREATE INDEX idx_model_versions_created ON model_versions (created_ts DESC);"
    )

    op.execute("""
    CREATE TABLE model_inferences (
        inference_id         UUID         NOT NULL DEFAULT uuid_generate_v4(),
        model_version_id     UUID         NOT NULL,
        market_id            TEXT         NOT NULL,
        event_id             TEXT         NOT NULL,
        inference_ts         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        asof_ts              TIMESTAMPTZ  NOT NULL,
        p_home               NUMERIC(8,6),
        p_draw               NUMERIC(8,6),
        p_away               NUMERIC(8,6),
        feature_vector_ids   UUID[]       NOT NULL,
        features_used        JSONB        NOT NULL,
        PRIMARY KEY (inference_id)
    );
    """)
    op.execute(
        "CREATE INDEX idx_model_inferences_market "
        "ON model_inferences (market_id, inference_ts);"
    )
    op.execute(
        "CREATE INDEX idx_model_inferences_version "
        "ON model_inferences (model_version_id);"
    )

    op.execute("ALTER TABLE decisions ADD COLUMN inference_id UUID;")
    op.execute(
        "CREATE INDEX idx_decisions_inference "
        "ON decisions (inference_id) WHERE inference_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_decisions_inference;")
    op.execute("ALTER TABLE decisions DROP COLUMN IF EXISTS inference_id;")
    op.execute("DROP TABLE IF EXISTS model_inferences;")
    op.execute("DROP TABLE IF EXISTS model_versions;")
```

- [ ] **Step 2: Update `tests/integration/test_pg_smoke.py`**

Find the `expected` set in `test_schema_tables_exist` and add `"model_versions"`, `"model_inferences"`:

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
    }
```

- [ ] **Step 3: Update `tests/integration/conftest.py`**

Find the TRUNCATE statement in `clean_db` and add the two new tables:

```python
    await conn.execute(
        "TRUNCATE markets, runners, market_snapshots, "
        "external_feature_snapshots, feature_vectors, config_snapshots, "
        "decisions, model_versions, model_inferences "
        "RESTART IDENTITY CASCADE"
    )
```

- [ ] **Step 4: Run smoke + integration suite**

Run: `uv run pytest tests/integration/test_pg_smoke.py -v -m integration`
Expected: 4 PASS (includes the updated schema check).

Run: `uv run pytest -v -m integration`
Expected: 33 existing integration tests still pass.

Run: `uv run pytest -v -m "not integration"`
Expected: 63 unit tests still pass.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/003_model_versions_inferences.py tests/integration/test_pg_smoke.py tests/integration/conftest.py
git commit -m "feat(db): add model_versions, model_inferences, decisions.inference_id"
```

---

## Task 2: Add ML dependencies + scaffold dirs

**Files:**
- Modify: `pyproject.toml`
- Create: `src/betfair_trading/training/__init__.py`
- Create: `models/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Update `pyproject.toml`**

Add to `dependencies` (NOT to dev deps — sklearn/joblib are needed for inference at runtime):

```toml
dependencies = [
    "betfairlightweight>=2.20.1",
    "asyncpg>=0.30.0",
    "sqlalchemy>=2.0.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    "structlog>=24.4.0",
    "pyyaml>=6.0",
    "aiohttp>=3.11.0",
    "alembic>=1.14.0",
    "scikit-learn>=1.5.0",
    "numpy>=2.0.0",
    "joblib>=1.4.0",
]
```

- [ ] **Step 2: Create empty package init**

Create `src/betfair_trading/training/__init__.py` (empty file).

- [ ] **Step 3: Create models/ directory with .gitkeep**

Create `models/.gitkeep` (empty file).

- [ ] **Step 4: Update `.gitignore`**

Append to `.gitignore`:

```
# Trained model artifacts (kept on disk, registered in model_versions table)
models/*.joblib
```

- [ ] **Step 5: Sync deps**

Run: `uv sync --all-extras`
Expected: installs scikit-learn, numpy, joblib + transitives without errors.

- [ ] **Step 6: Verify no regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 63 unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/betfair_trading/training/__init__.py models/.gitkeep .gitignore
git commit -m "feat: add scikit-learn/numpy/joblib + scaffold training/ and models/"
```

---

## Task 3: Pydantic contracts (ModelVersion, ModelInference, Decision.inference_id)

**Files:**
- Create: `src/betfair_trading/models/inference.py`
- Modify: `src/betfair_trading/models/decision.py`
- Create: `tests/unit/test_inference_model.py`
- Modify: `tests/unit/test_decision_model.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_inference_model.py`:

```python
"""Unit tests for ModelVersion and ModelInference Pydantic contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from betfair_trading.models.inference import ModelInference, ModelVersion


def test_model_version_full_construction():
    mv = ModelVersion(
        model_name="logistic_v1",
        feature_set_version="A2_EXT_ONLY",
        file_path="models/logistic_v1_20260511.joblib",
        training_data_hash="abc123",
        training_csv_path="data/results.csv",
        training_params={"C": 1.0, "calibration": "sigmoid"},
        metrics={"log_loss": 1.05, "accuracy": 0.45},
        feature_names=["elo_home", "elo_away", "elo_delta"],
        n_train=800,
        n_test=200,
    )
    assert mv.model_name == "logistic_v1"
    assert mv.feature_set_version == "A2_EXT_ONLY"
    assert mv.n_train == 800
    assert mv.created_ts is None  # DB default


def test_model_inference_full_construction():
    fv_id = uuid4()
    mv_id = uuid4()
    mi = ModelInference(
        model_version_id=mv_id,
        market_id="1.A",
        event_id="E-A",
        asof_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        p_home=Decimal("0.550000"),
        p_draw=Decimal("0.250000"),
        p_away=Decimal("0.200000"),
        feature_vector_ids=[fv_id],
        features_used={"elo_home": 1510.0, "elo_away": 1490.0},
    )
    assert mi.model_version_id == mv_id
    assert mi.p_home == Decimal("0.550000")
    assert mi.feature_vector_ids == [fv_id]
```

Append to `tests/unit/test_decision_model.py` (after the existing tests):

```python
def test_decision_with_inference_id():
    from uuid import uuid4
    inf_id = uuid4()
    fv_id = uuid4()
    d = Decision(
        market_id="1.A",
        event_id="E-A",
        decision_ts=datetime(2026, 5, 11, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=DecisionOutcome.ALLOW,
        feature_vector_ids=[fv_id],
        inference_id=inf_id,
    )
    assert d.inference_id == inf_id


def test_decision_inference_id_optional():
    fv_id = uuid4()
    d = Decision(
        market_id="1.A",
        event_id="E-A",
        decision_ts=datetime(2026, 5, 11, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=DecisionOutcome.ALLOW,
        feature_vector_ids=[fv_id],
    )
    assert d.inference_id is None
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_inference_model.py tests/unit/test_decision_model.py -v -m "not integration"`
Expected: 2 FAIL (test_inference_model imports missing module) + 2 FAIL (Decision doesn't have inference_id field yet).

- [ ] **Step 3: Create `models/inference.py`**

Create `src/betfair_trading/models/inference.py`:

```python
"""Pydantic contracts for ModelVersion and ModelInference."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ModelVersion(BaseModel):
    model_version_id: UUID = Field(default_factory=uuid4)
    model_name: str
    feature_set_version: str
    created_ts: datetime | None = None  # DB default NOW()
    file_path: str
    training_data_hash: str
    training_csv_path: str
    training_params: dict
    metrics: dict
    feature_names: list[str]
    n_train: int
    n_test: int


class ModelInference(BaseModel):
    inference_id: UUID = Field(default_factory=uuid4)
    model_version_id: UUID
    market_id: str
    event_id: str
    inference_ts: datetime | None = None  # DB default NOW()
    asof_ts: datetime
    p_home: Decimal
    p_draw: Decimal
    p_away: Decimal
    feature_vector_ids: list[UUID]
    features_used: dict[str, float]
```

- [ ] **Step 4: Update `models/decision.py`**

Open `src/betfair_trading/models/decision.py`. Find the `Decision` class and add the `inference_id` field at the end (after `config_snapshot_id`):

```python
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
    inference_id: UUID | None = None  # NEW
```

- [ ] **Step 5: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_inference_model.py tests/unit/test_decision_model.py -v -m "not integration"`
Expected: all PASS (2 new in test_inference_model + 2 new in test_decision_model + 3 pre-existing in test_decision_model).

- [ ] **Step 6: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 67 unit tests pass (63 + 2 inference + 2 decision new).

- [ ] **Step 7: Commit**

```bash
git add src/betfair_trading/models/inference.py src/betfair_trading/models/decision.py tests/unit/test_inference_model.py tests/unit/test_decision_model.py
git commit -m "feat(models): add ModelVersion, ModelInference + Decision.inference_id"
```

---

## Task 4: DB writers (insert_model_version, insert_model_inference, update insert_decision)

**Files:**
- Modify: `src/betfair_trading/db/writer.py`
- Create: `tests/integration/test_inference_writers.py`
- Modify: `tests/integration/test_decision_writer.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_inference_writers.py`:

```python
"""Integration tests for insert_model_version and insert_model_inference."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg

from betfair_trading.db.writer import insert_model_inference, insert_model_version
from betfair_trading.models.inference import ModelInference, ModelVersion


async def test_insert_model_version_persists(pg_pool: asyncpg.Pool):
    mv = ModelVersion(
        model_name="logistic_v1",
        feature_set_version="A2_EXT_ONLY",
        file_path="models/logistic_v1.joblib",
        training_data_hash="abc123",
        training_csv_path="data/results.csv",
        training_params={"C": 1.0},
        metrics={"log_loss": 1.05},
        feature_names=["elo_home", "elo_away"],
        n_train=80,
        n_test=20,
    )
    async with pg_pool.acquire() as conn:
        mv_id = await insert_model_version(conn, mv)
    assert mv_id == mv.model_version_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM model_versions WHERE model_version_id = $1", mv_id
        )
    assert row["model_name"] == "logistic_v1"
    assert row["feature_set_version"] == "A2_EXT_ONLY"
    assert row["n_train"] == 80
    params = row["training_params"]
    if isinstance(params, str):
        params = json.loads(params)
    assert params["C"] == 1.0
    fnames = row["feature_names"]
    if isinstance(fnames, str):
        fnames = json.loads(fnames)
    assert fnames == ["elo_home", "elo_away"]


async def test_insert_model_inference_persists(pg_pool: asyncpg.Pool):
    fv_id = uuid4()
    mv_id = uuid4()
    mi = ModelInference(
        model_version_id=mv_id,
        market_id="1.A",
        event_id="E-A",
        asof_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        p_home=Decimal("0.550000"),
        p_draw=Decimal("0.250000"),
        p_away=Decimal("0.200000"),
        feature_vector_ids=[fv_id],
        features_used={"elo_home": 1510.0, "elo_away": 1490.0},
    )
    async with pg_pool.acquire() as conn:
        inf_id = await insert_model_inference(conn, mi)
    assert inf_id == mi.inference_id

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM model_inferences WHERE inference_id = $1", inf_id
        )
    assert row["market_id"] == "1.A"
    assert row["p_home"] == Decimal("0.550000")
    assert list(row["feature_vector_ids"]) == [fv_id]
    feats = row["features_used"]
    if isinstance(feats, str):
        feats = json.loads(feats)
    assert feats["elo_home"] == 1510.0
```

Append to `tests/integration/test_decision_writer.py` (at the end):

```python
async def test_insert_decision_with_inference_id(pg_pool: asyncpg.Pool):
    """The new inference_id column persists round-trip."""
    fv_id = uuid4()
    inf_id = uuid4()
    decision = Decision(
        market_id="1.A",
        event_id="E-A",
        decision_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        model_version="logistic_v1",
        p_model={101: 0.55, 102: 0.25, 103: 0.20},
        p_market={101: 0.50, 102: 0.30, 103: 0.20},
        edge_gross={101: 0.05, 102: -0.05, 103: 0.0},
        edge_net={101: 0.0225, 102: -0.0625, 103: -0.01},
        gate_results={"kill_switch": GateResult(passed=True, reason="ok")},
        decision_outcome=DecisionOutcome.ALLOW,
        feature_vector_ids=[fv_id],
        inference_id=inf_id,
    )
    async with pg_pool.acquire() as conn:
        decision_id = await insert_decision(conn, decision)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT inference_id FROM decisions WHERE decision_id = $1", decision_id
        )
    assert row["inference_id"] == inf_id
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_inference_writers.py tests/integration/test_decision_writer.py -v -m integration`
Expected: 2 FAIL (writers don't exist) + 1 FAIL (insert_decision doesn't handle inference_id).

- [ ] **Step 3: Add writers + update insert_decision**

Modify `src/betfair_trading/db/writer.py`.

Add import at top:

```python
from betfair_trading.models.inference import ModelInference, ModelVersion
```

Update the existing `insert_decision` to include `inference_id` as the 18th column. Find the SQL and the parameters list:

```python
async def insert_decision(conn: asyncpg.Connection, decision: Decision) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO decisions
           (decision_id, market_id, event_id, snapshot_id, decision_ts,
            model_version, p_model, p_market, edge_gross, edge_net,
            selected_runner_id, selected_edge_net,
            gate_results, decision_outcome, rationale,
            feature_vector_ids, config_snapshot_id, inference_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
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
        decision.inference_id,
    )
```

Append the 2 new writers at the end of `writer.py`:

```python
async def insert_model_version(
    conn: asyncpg.Connection, mv: ModelVersion
) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO model_versions
           (model_version_id, model_name, feature_set_version,
            file_path, training_data_hash, training_csv_path,
            training_params, metrics, feature_names, n_train, n_test)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING model_version_id""",
        mv.model_version_id,
        mv.model_name,
        mv.feature_set_version,
        mv.file_path,
        mv.training_data_hash,
        mv.training_csv_path,
        json.dumps(mv.training_params, default=str),
        json.dumps(mv.metrics, default=str),
        json.dumps(mv.feature_names),
        mv.n_train,
        mv.n_test,
    )


async def insert_model_inference(
    conn: asyncpg.Connection, mi: ModelInference
) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO model_inferences
           (inference_id, model_version_id, market_id, event_id, asof_ts,
            p_home, p_draw, p_away, feature_vector_ids, features_used)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           RETURNING inference_id""",
        mi.inference_id,
        mi.model_version_id,
        mi.market_id,
        mi.event_id,
        mi.asof_ts,
        mi.p_home,
        mi.p_draw,
        mi.p_away,
        mi.feature_vector_ids,
        json.dumps(mi.features_used, default=str),
    )
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_inference_writers.py tests/integration/test_decision_writer.py -v -m integration`
Expected: all PASS (2 inference writers + 2 decision tests, one new and one pre-existing).

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m integration`
Expected: 35 integration tests pass (33 prior + 2 new).

Run: `uv run pytest -v -m "not integration"`
Expected: 67 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/db/writer.py tests/integration/test_inference_writers.py tests/integration/test_decision_writer.py
git commit -m "feat(db): add insert_model_{version,inference}; update insert_decision for inference_id"
```

---

## Task 5: training/features.py — shared feature schema

**Files:**
- Create: `src/betfair_trading/training/features.py`
- Create: `tests/unit/test_training_features.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_training_features.py`:

```python
"""Unit tests for the shared feature schema (training/features.py)."""

import math

import numpy as np

from betfair_trading.training.features import (
    FEATURE_NAMES,
    build_feature_dict,
    feature_dict_to_array,
)


def test_feature_names_count_and_order():
    assert FEATURE_NAMES[0] == "elo_home"
    assert FEATURE_NAMES[1] == "elo_away"
    assert FEATURE_NAMES[2] == "elo_delta"
    assert len(FEATURE_NAMES) == 15


def test_build_feature_dict_complete_values():
    values = {
        "elo_home": 1510.0, "elo_away": 1490.0, "elo_delta": 20.0,
        "form_home_5_ppm": 2.0, "form_away_5_ppm": 1.5,
        "form_home_5_gd": 1.5, "form_away_5_gd": -0.5,
        "form_home_5_wr": 0.6, "form_away_5_wr": 0.4,
        "form_home_10_ppm": 1.8, "form_away_10_ppm": 1.2,
        "form_home_10_gd": 1.0, "form_away_10_gd": -0.2,
        "form_home_10_wr": 0.5, "form_away_10_wr": 0.3,
    }
    d = build_feature_dict(values)
    assert set(d.keys()) == set(FEATURE_NAMES)
    assert d["elo_home"] == 1510.0
    assert d["form_home_5_ppm"] == 2.0


def test_build_feature_dict_none_replaced_with_zero():
    values = {
        "elo_home": 1500.0, "elo_away": 1500.0, "elo_delta": 0.0,
        "form_home_5_ppm": None, "form_away_5_ppm": None,
        # All other form features unset → defaults
    }
    d = build_feature_dict(values)
    assert d["form_home_5_ppm"] == 0.0
    assert d["form_away_5_gd"] == 0.0  # missing key → 0.0


def test_build_feature_dict_elo_delta_auto_computed():
    values = {"elo_home": 1510.0, "elo_away": 1490.0}  # delta absent
    d = build_feature_dict(values)
    assert math.isclose(d["elo_delta"], 20.0)


def test_feature_dict_to_array_shape_and_order():
    d = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
    arr = feature_dict_to_array(d)
    assert arr.shape == (1, 15)
    assert arr[0, 0] == 0.0  # elo_home
    assert arr[0, 1] == 1.0  # elo_away
    assert arr[0, 14] == 14.0  # form_away_10_wr


def test_zero_skew_train_vs_inference_extraction():
    """Same underlying state → same feature dict, regardless of input shape."""
    # Training shape: scalars derived from FormFeatures objects
    train_values = {
        "elo_home": 1510.5, "elo_away": 1490.5,
        "form_home_5_ppm": 2.0, "form_home_5_gd": 1.5, "form_home_5_wr": 0.6,
        "form_away_5_ppm": 1.0, "form_away_5_gd": -1.0, "form_away_5_wr": 0.3,
    }
    # Inference shape: same scalars extracted from A2 JSONB dict
    inf_values = {
        "elo_home": 1510.5, "elo_away": 1490.5,
        "elo_delta": 20.0,  # explicit, matches train auto-compute
        "form_home_5_ppm": 2.0, "form_home_5_gd": 1.5, "form_home_5_wr": 0.6,
        "form_away_5_ppm": 1.0, "form_away_5_gd": -1.0, "form_away_5_wr": 0.3,
    }
    d_train = build_feature_dict(train_values)
    d_inf = build_feature_dict(inf_values)
    assert d_train == d_inf
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_training_features.py -v -m "not integration"`
Expected: 6 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `training/features.py`**

Create `src/betfair_trading/training/features.py`:

```python
"""Shared feature schema for training and inference. Single source of truth.

Any change here requires retraining: the model's input dimension and ordering
is fixed by FEATURE_NAMES.
"""

import numpy as np

FEATURE_NAMES: list[str] = [
    "elo_home",
    "elo_away",
    "elo_delta",
    "form_home_5_ppm",
    "form_away_5_ppm",
    "form_home_5_gd",
    "form_away_5_gd",
    "form_home_5_wr",
    "form_away_5_wr",
    "form_home_10_ppm",
    "form_away_10_ppm",
    "form_home_10_gd",
    "form_away_10_gd",
    "form_home_10_wr",
    "form_away_10_wr",
]


def build_feature_dict(values: dict[str, float | None]) -> dict[str, float]:
    """Normalize a feature values dict to FEATURE_NAMES order, replacing None with 0.0.

    If elo_delta is absent but elo_home and elo_away are present, it is auto-computed.
    """
    if (
        "elo_delta" not in values
        and values.get("elo_home") is not None
        and values.get("elo_away") is not None
    ):
        values = {**values, "elo_delta": float(values["elo_home"]) - float(values["elo_away"])}
    out: dict[str, float] = {}
    for name in FEATURE_NAMES:
        v = values.get(name)
        out[name] = float(v) if v is not None else 0.0
    return out


def feature_dict_to_array(d: dict[str, float]) -> np.ndarray:
    """Shape (1, len(FEATURE_NAMES)) in FEATURE_NAMES order, ready for predict_proba."""
    return np.array([[d[name] for name in FEATURE_NAMES]])
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_training_features.py -v -m "not integration"`
Expected: 6 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 73 unit tests pass (67 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/training/features.py tests/unit/test_training_features.py
git commit -m "feat(training): add shared feature schema (FEATURE_NAMES + build_feature_dict)"
```

---

## Task 6: training/dataset.py — DatasetBuilder

**Files:**
- Create: `src/betfair_trading/training/dataset.py`
- Create: `tests/unit/test_dataset_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dataset_builder.py`:

```python
"""Unit tests for DatasetBuilder (CSV replay)."""

import csv
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from betfair_trading.training.dataset import DatasetBuilder
from betfair_trading.training.features import FEATURE_NAMES


def _write_csv(p: Path, rows: list[tuple]) -> None:
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for r in rows:
            w.writerow(r)


def test_build_emits_n_rows_for_n_matches(tmp_path):
    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        ("01/01/2025", "Liverpool", "Arsenal", "H", 2, 0),
        ("08/01/2025", "Arsenal", "Liverpool", "D", 1, 1),
        ("15/01/2025", "Chelsea", "Liverpool", "A", 0, 2),
        ("22/01/2025", "Arsenal", "Chelsea", "H", 3, 1),
    ])
    builder = DatasetBuilder()
    X, y, dates = builder.build(csv_path)
    assert X.shape == (4, len(FEATURE_NAMES))
    assert y.shape == (4,)
    assert len(dates) == 4


def test_build_labels_mapped_correctly(tmp_path):
    csv_path = tmp_path / "results.csv"
    _write_csv(csv_path, [
        ("01/01/2025", "A", "B", "H", 1, 0),
        ("08/01/2025", "A", "B", "D", 1, 1),
        ("15/01/2025", "A", "B", "A", 0, 1),
    ])
    builder = DatasetBuilder()
    _X, y, _dates = builder.build(csv_path)
    assert list(y) == [0, 1, 2]  # H, D, A


def test_build_anti_leakage(tmp_path):
    """The features of match i must not reflect the result of match i."""
    csv_path = tmp_path / "results.csv"
    # Two Liverpool-vs-Arsenal matches; second one's Elo must equal the
    # post-first-match ratings (not post-second-match).
    _write_csv(csv_path, [
        ("01/01/2025", "Liverpool", "Arsenal", "H", 2, 0),  # match 1
        ("08/01/2025", "Liverpool", "Arsenal", "H", 3, 0),  # match 2
    ])
    builder = DatasetBuilder()
    X, _y, _dates = builder.build(csv_path)

    # Match 1: both teams at initial 1500 → features 1500/1500/0
    assert X[0, 0] == 1500.0  # elo_home
    assert X[0, 1] == 1500.0  # elo_away
    assert X[0, 2] == 0.0     # elo_delta

    # Match 2: Liverpool won match 1 → Elo_home > 1500, Elo_away < 1500
    # (but NOT yet reflecting match 2's result)
    assert X[1, 0] > 1500.0
    assert X[1, 1] < 1500.0
    # After applying match 2, Liverpool would be even higher; this check confirms
    # we read features BEFORE applying match 2.
    # Expected after match 1: home_elo = 1500 + 20*(1 - 0.5) = 1510
    assert abs(X[1, 0] - 1510.0) < 0.01
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/unit/test_dataset_builder.py -v -m "not integration"`
Expected: 3 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `training/dataset.py`**

Create `src/betfair_trading/training/dataset.py`:

```python
"""Dataset builder: replay temporale del CSV per generare (X, y, dates).

Riusa EloEngine + FormCalculator per garantire le stesse semantiche as-of
del runtime live (anti-leakage).
"""

import csv
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from betfair_trading.elo.engine import EloEngine, MatchResult
from betfair_trading.elo.form import FormCalculator
from betfair_trading.training.features import (
    FEATURE_NAMES,
    build_feature_dict,
)


_RESULT_TO_INT: dict[str, int] = {"H": 0, "D": 1, "A": 2}
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y")


def _parse_date(s: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class DatasetBuilder:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.elo = EloEngine(k_factor=k_factor, initial_rating=initial_rating)
        self.form = FormCalculator()

    def build(
        self, csv_path: Path
    ) -> tuple[np.ndarray, np.ndarray, list[datetime]]:
        """Iterate the CSV chronologically. For each match:
        1. Read features as-of pre-kickoff.
        2. Append to dataset.
        3. Apply match result to engines (anti-leakage: AFTER reading).

        Returns (X, y, dates). X: (n_samples, len(FEATURE_NAMES)). y: int labels [0=H, 1=D, 2=A].
        """
        matches: list[tuple[datetime, str, str, str, int, int]] = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dt = _parse_date(row.get("Date", "") or "")
                if dt is None:
                    continue
                home = (row.get("HomeTeam") or "").strip()
                away = (row.get("AwayTeam") or "").strip()
                ftr = (row.get("FTR") or "").strip()
                try:
                    fthg = int(row.get("FTHG", 0) or 0)
                    ftag = int(row.get("FTAG", 0) or 0)
                except ValueError:
                    continue
                if not (home and away and ftr in _RESULT_TO_INT):
                    continue
                matches.append((dt, home, away, ftr, fthg, ftag))

        matches.sort(key=lambda m: m[0])

        X_rows: list[list[float]] = []
        y_rows: list[int] = []
        dates: list[datetime] = []

        for dt, home, away, ftr, fthg, ftag in matches:
            elo_h, elo_a = self.elo.get_ratings_asof(home, away, dt)
            fh5 = self.form.compute_form(home, dt, 5)
            fa5 = self.form.compute_form(away, dt, 5)
            fh10 = self.form.compute_form(home, dt, 10)
            fa10 = self.form.compute_form(away, dt, 10)

            values: dict[str, float | None] = {
                "elo_home": elo_h,
                "elo_away": elo_a,
                "form_home_5_ppm": fh5.points_per_match if fh5 else None,
                "form_away_5_ppm": fa5.points_per_match if fa5 else None,
                "form_home_5_gd": fh5.goal_diff_per_match if fh5 else None,
                "form_away_5_gd": fa5.goal_diff_per_match if fa5 else None,
                "form_home_5_wr": fh5.win_rate if fh5 else None,
                "form_away_5_wr": fa5.win_rate if fa5 else None,
                "form_home_10_ppm": fh10.points_per_match if fh10 else None,
                "form_away_10_ppm": fa10.points_per_match if fa10 else None,
                "form_home_10_gd": fh10.goal_diff_per_match if fh10 else None,
                "form_away_10_gd": fa10.goal_diff_per_match if fa10 else None,
                "form_home_10_wr": fh10.win_rate if fh10 else None,
                "form_away_10_wr": fa10.win_rate if fa10 else None,
            }
            d = build_feature_dict(values)
            X_rows.append([d[name] for name in FEATURE_NAMES])
            y_rows.append(_RESULT_TO_INT[ftr])
            dates.append(dt)

            # Apply result AFTER reading features (anti-leakage)
            result = MatchResult(ftr)
            self.elo.apply_result(home, away, result, dt)
            self.form.add_match(home, away, result, fthg, ftag, dt)

        return np.array(X_rows), np.array(y_rows), dates
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/unit/test_dataset_builder.py -v -m "not integration"`
Expected: 3 PASS.

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v -m "not integration"`
Expected: 76 unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/training/dataset.py tests/unit/test_dataset_builder.py
git commit -m "feat(training): add DatasetBuilder with anti-leakage replay"
```

---

## Task 7: training/train.py — CLI entrypoint

**Files:**
- Create: `src/betfair_trading/training/train.py`
- Create: `tests/integration/test_train_cli.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_train_cli.py`:

```python
"""Integration test for the train CLI."""

import csv
import json
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.training.train import main as train_main


def _make_results_csv(p: Path, n_matches: int) -> Path:
    teams = [f"Team{i}" for i in range(10)]
    random.seed(42)
    base_date = datetime(2025, 1, 1, tzinfo=UTC)
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for i in range(n_matches):
            dt = base_date + timedelta(days=i)
            home = random.choice(teams)
            away = random.choice([t for t in teams if t != home])
            ftr = random.choice(["H", "D", "A"])
            fthg = random.randint(0, 4)
            ftag = random.randint(0, 4)
            w.writerow([dt.strftime("%d/%m/%Y"), home, away, ftr, fthg, ftag])
    return p


@pytest.fixture
def synthetic_csv(tmp_path: Path) -> Path:
    return _make_results_csv(tmp_path / "results.csv", n_matches=120)


async def test_train_end_to_end(
    pg_pool: asyncpg.Pool, synthetic_csv: Path, tmp_path: Path, monkeypatch
):
    output_dir = tmp_path / "models"
    output_dir.mkdir()

    # train_main reads DATABASE_URL from env, but we already have a pool — use its connection url.
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])

    await train_main(
        csv_path=synthetic_csv,
        model_name="test_v1",
        output_dir=output_dir,
        test_size=0.2,
    )

    # Joblib artifact created
    joblib_files = list(output_dir.glob("*.joblib"))
    assert len(joblib_files) == 1
    artifact_path = joblib_files[0]

    # model_versions row inserted
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM model_versions WHERE model_name = 'test_v1'"
        )
    assert row is not None
    assert row["feature_set_version"] == "A2_EXT_ONLY"
    assert row["file_path"].endswith(artifact_path.name)
    # CSV hash matches
    import hashlib
    expected_hash = hashlib.sha256(synthetic_csv.read_bytes()).hexdigest()
    assert row["training_data_hash"] == expected_hash


async def test_train_temporal_split_respected(
    pg_pool: asyncpg.Pool, synthetic_csv: Path, tmp_path: Path, monkeypatch
):
    output_dir = tmp_path / "models"
    output_dir.mkdir()
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])

    # 120 matches, test_size=0.2 → 96 train / 24 test
    await train_main(
        csv_path=synthetic_csv,
        model_name="temporal_split_v1",
        output_dir=output_dir,
        test_size=0.2,
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT n_train, n_test FROM model_versions WHERE model_name = 'temporal_split_v1'"
        )
    assert row["n_train"] == 96
    assert row["n_test"] == 24
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_train_cli.py -v -m integration`
Expected: 2 FAIL with `ModuleNotFoundError` for `betfair_trading.training.train`.

- [ ] **Step 3: Create `training/train.py`**

Create `src/betfair_trading/training/train.py`:

```python
"""CLI: train and persist a baseline LogisticRegression model.

Usage:
    uv run python -m betfair_trading.training.train \\
        --csv-path data/results.csv \\
        --model-name logistic_v1 \\
        --output-dir models/ \\
        --test-size 0.2
"""

import argparse
import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from betfair_trading.db.writer import insert_model_version
from betfair_trading.models.inference import ModelVersion
from betfair_trading.training.dataset import DatasetBuilder
from betfair_trading.training.features import FEATURE_NAMES


async def main(
    csv_path: Path,
    model_name: str,
    output_dir: Path,
    test_size: float = 0.2,
) -> None:
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build dataset
    builder = DatasetBuilder()
    X, y, dates = builder.build(csv_path)
    n = len(X)
    if n == 0:
        raise SystemExit("Dataset is empty — no valid matches in CSV")

    # 2. Temporal split (NOT random)
    split_idx = int(n * (1 - test_size))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    # 3. Pipeline + Platt calibration
    base = LogisticRegression(solver="lbfgs", max_iter=1000, C=1.0)
    pipe = Pipeline([("scaler", StandardScaler()), ("clf", base)])
    model = CalibratedClassifierCV(pipe, method="sigmoid", cv=5)

    # 4. Fit
    model.fit(X_train, y_train)

    # 5. Eval
    proba_test = model.predict_proba(X_test)
    pred_test = model.predict(X_test)
    metrics = {
        "log_loss": float(log_loss(y_test, proba_test)),
        "accuracy": float(accuracy_score(y_test, pred_test)),
        "brier_home": float(
            brier_score_loss((y_test == 0).astype(int), proba_test[:, 0])
        ),
        "brier_draw": float(
            brier_score_loss((y_test == 1).astype(int), proba_test[:, 1])
        ),
        "brier_away": float(
            brier_score_loss((y_test == 2).astype(int), proba_test[:, 2])
        ),
        "confusion_matrix": confusion_matrix(y_test, pred_test).tolist(),
    }

    # 6. Save joblib artifact
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    artifact_filename = f"{model_name}_{timestamp}.joblib"
    artifact_path = output_dir / artifact_filename
    joblib.dump(model, artifact_path)

    # 7. SHA256 of input CSV
    training_data_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    # 8. INSERT model_versions
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set; cannot persist model_versions")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await insert_model_version(
                conn,
                ModelVersion(
                    model_name=model_name,
                    feature_set_version="A2_EXT_ONLY",
                    file_path=str(artifact_path),
                    training_data_hash=training_data_hash,
                    training_csv_path=str(csv_path),
                    training_params={
                        "solver": "lbfgs",
                        "C": 1.0,
                        "max_iter": 1000,
                        "calibration": "sigmoid",
                        "cv": 5,
                    },
                    metrics=metrics,
                    feature_names=FEATURE_NAMES,
                    n_train=int(len(X_train)),
                    n_test=int(len(X_test)),
                ),
            )
    finally:
        await pool.close()

    print(f"Trained '{model_name}': n_train={len(X_train)}, n_test={len(X_test)}")
    print(f"  log_loss={metrics['log_loss']:.4f} accuracy={metrics['accuracy']:.4f}")
    print(f"  artifact={artifact_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv-path", type=Path, required=True)
    p.add_argument("--model-name", type=str, default="logistic_v1")
    p.add_argument("--output-dir", type=Path, default=Path("models"))
    p.add_argument("--test-size", type=float, default=0.2)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            csv_path=args.csv_path,
            model_name=args.model_name,
            output_dir=args.output_dir,
            test_size=args.test_size,
        )
    )
```

- [ ] **Step 4: Run tests (must pass)**

Run: `uv run pytest tests/integration/test_train_cli.py -v -m integration`
Expected: 2 PASS. The training takes a few seconds (CalibratedClassifierCV does cv=5 folds).

- [ ] **Step 5: No regressions**

Run: `uv run pytest -v`
Expected: 76 unit + 37 integration = 113 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/training/train.py tests/integration/test_train_cli.py
git commit -m "feat(training): add train CLI with Platt-calibrated LogReg on Elo+form features"
```

---

## Task 8: Breaking change to ProbabilityProvider Protocol + stubs

**Files:**
- Modify: `src/betfair_trading/services/probability_providers.py`
- Modify: `src/betfair_trading/services/decision_engine.py`
- Modify: `tests/unit/test_probability_providers.py`
- Modify: `tests/integration/test_pipeline_decision.py`

- [ ] **Step 1: Update the Protocol and stubs**

Open `src/betfair_trading/services/probability_providers.py`.

Change the Protocol signature:

```python
class ProbabilityProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], uuid.UUID | None]: ...
```

Update `MarketImpliedProvider.get_probabilities`:

```python
    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], uuid.UUID | None]:
        return compute_market_probs(_runner_quotes(bundle, runners)), None
```

Update `BiasedStubProvider.get_probabilities`:

```python
    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], uuid.UUID | None]:
        market_probs = compute_market_probs(_runner_quotes(bundle, runners))

        sorted_runners = sorted(
            runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
        )
        if not sorted_runners:
            return market_probs, None
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
            return market_probs, None
        return {rid: p / total for rid, p in biased.items()}, None
```

- [ ] **Step 2: Update DecisionEngine.evaluate**

In `src/betfair_trading/services/decision_engine.py`, find:

```python
            p_model = await self._provider.get_probabilities(
                bundle, runners_meta, feature_vector_ids
            )
```

Replace with:

```python
            p_model, inference_id = await self._provider.get_probabilities(
                bundle, runners_meta, feature_vector_ids
            )
```

Find the `Decision(...)` constructor call and add `inference_id=inference_id,` to the kwargs. Locate it just before the existing `feature_vector_ids=feature_vector_ids,` line, or anywhere in the field list:

```python
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
                inference_id=inference_id,
            )
```

- [ ] **Step 3: Update existing unit tests for tuple return**

In `tests/unit/test_probability_providers.py`, find every call to `provider.get_probabilities(...)` and update the assertions to unpack the tuple.

For `test_market_implied_provider_returns_normalized_probs`:
```python
async def test_market_implied_provider_returns_normalized_probs():
    bundle, runners = _make_bundle_and_runners()
    provider = MarketImpliedProvider()
    probs, inference_id = await provider.get_probabilities(bundle, runners, feature_vector_ids=[])

    assert inference_id is None
    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert probs[101] > probs[102]
    assert probs[101] > probs[103]
```

For `test_biased_stub_provider_shifts_home`:
```python
async def test_biased_stub_provider_shifts_home():
    bundle, runners = _make_bundle_and_runners()
    market_provider = MarketImpliedProvider()
    market_probs, _ = await market_provider.get_probabilities(bundle, runners, [])

    biased_provider = BiasedStubProvider(home_bias=0.05)
    biased_probs, inference_id = await biased_provider.get_probabilities(bundle, runners, [])

    assert inference_id is None
    assert biased_probs[101] > market_probs[101]
    total = sum(biased_probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
```

The two `*_version` tests don't call `get_probabilities` and need no change.

- [ ] **Step 4: Run unit tests (must pass)**

Run: `uv run pytest tests/unit -v -m "not integration"`
Expected: 76 PASS (the 4 probability_providers tests now use tuple unpack).

- [ ] **Step 5: Run integration tests (must pass — DecisionEngine + tests should still work)**

Run: `uv run pytest tests/integration/test_pipeline_decision.py -v -m integration`
Expected: 7 PASS. The existing Decision Engine tests work because we kept `inference_id` optional in Decision and added it to the writer (Task 4).

If a test fails (e.g., because of asyncpg type issue when inference_id is None in stub paths), inspect the failure. It should not fail given Task 4 added `decision.inference_id` to the INSERT bindings.

Run: `uv run pytest -v -m integration`
Expected: 37 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/betfair_trading/services/probability_providers.py src/betfair_trading/services/decision_engine.py tests/unit/test_probability_providers.py
git commit -m "feat(decision): ProbabilityProvider returns tuple (probs, inference_id); update stubs and DecisionEngine"
```

---

## Task 9: ModelInferenceProvider + wire main.py + 5 integration tests

**Files:**
- Create: `src/betfair_trading/services/model_inference_provider.py`
- Create: `tests/integration/test_model_inference_provider.py`
- Modify: `src/betfair_trading/main.py`

- [ ] **Step 1: Write the 5 failing integration tests**

Create `tests/integration/test_model_inference_provider.py`:

```python
"""Integration tests for ModelInferenceProvider."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import joblib
import numpy as np
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from betfair_trading.db.writer import insert_model_version
from betfair_trading.models.inference import ModelVersion
from betfair_trading.services.decision_engine import DecisionEngine
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from betfair_trading.services.model_inference_provider import ModelInferenceProvider
from betfair_trading.training.features import FEATURE_NAMES
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


def _make_trained_model() -> CalibratedClassifierCV:
    """Train a trivial logistic on synthetic data so predict_proba returns valid probs."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, len(FEATURE_NAMES)))
    # 20 of each class
    y = np.array([0] * 20 + [1] * 20 + [2] * 20)
    base = LogisticRegression(solver="lbfgs", max_iter=200)
    pipe = Pipeline([("s", StandardScaler()), ("c", base)])
    model = CalibratedClassifierCV(pipe, method="sigmoid", cv=3)
    model.fit(X, y)
    return model


@pytest.fixture
def trained_model_on_disk(tmp_path: Path) -> Path:
    artifact = tmp_path / "test_model.joblib"
    joblib.dump(_make_trained_model(), artifact)
    return artifact


async def _seed_model_version(pg_pool, file_path: Path, model_name: str = "test_v1") -> ModelVersion:
    mv = ModelVersion(
        model_name=model_name,
        feature_set_version="A2_EXT_ONLY",
        file_path=str(file_path),
        training_data_hash="testhash",
        training_csv_path="fixture.csv",
        training_params={"C": 1.0},
        metrics={"log_loss": 1.05},
        feature_names=FEATURE_NAMES,
        n_train=40,
        n_test=20,
    )
    async with pg_pool.acquire() as conn:
        await insert_model_version(conn, mv)
    return mv


async def test_initialize_loads_latest_model(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    await _seed_model_version(pg_pool, trained_model_on_disk, model_name="test_v1")

    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

    assert provider.model_version == "test_v1"


async def test_initialize_no_model_falls_back(
    pg_pool: asyncpg.Pool, tmp_path: Path
):
    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

    assert provider.model_version == "STUB_NO_MODEL"

    # get_probabilities returns market-implied + None
    bundle, runners = _make_bundle_and_runners_for_test()
    probs, inference_id = await provider.get_probabilities(bundle, runners, feature_vector_ids=[])

    assert inference_id is None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


async def test_get_probabilities_persists_inference(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    mv = await _seed_model_version(pg_pool, trained_model_on_disk)

    # Set up market + A2 feature_vector via the real pipeline (need ingestor)
    from betfair_trading.elo.engine import EloEngine
    from betfair_trading.elo.form import FormCalculator
    from betfair_trading.entity_resolution.matcher import TeamMatcher
    from betfair_trading.services.external_ingestor import ExternalDataIngestor

    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n"Arsenal":\n  - "AFC"\n')
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher(mapping_yaml)
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)
    ingestor._loaded = True  # force history_loaded flag for completeness

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal",
                                start_time=datetime.now(UTC) + timedelta(minutes=60)))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    await collector.run_discovery()

    captured: dict = {}
    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        captured["bundle"] = bundle
        captured["fv_ids"] = fv_ids
    await collector.run_poll_cycle(on_snapshot=on_snap)

    # Now call the provider directly
    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

    # Build runners list (Runner Pydantic objects)
    from betfair_trading.models.market import Runner
    runners = [
        Runner(runner_id=101, runner_name="Liverpool", sort_priority=1),
        Runner(runner_id=102, runner_name="The Draw", sort_priority=2),
        Runner(runner_id=103, runner_name="Arsenal", sort_priority=3),
    ]

    probs, inference_id = await provider.get_probabilities(
        captured["bundle"], runners, captured["fv_ids"]
    )

    assert inference_id is not None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT model_version_id, market_id, p_home, p_draw, p_away "
            "FROM model_inferences WHERE inference_id = $1",
            inference_id,
        )
    assert row is not None
    assert row["model_version_id"] == mv.model_version_id
    assert row["market_id"] == "1.A"
    assert float(row["p_home"]) + float(row["p_draw"]) + float(row["p_away"]) == pytest.approx(1.0, abs=1e-6)


async def test_get_probabilities_falls_back_when_no_a2(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    """Model loaded but no A2 feature_vector available → market-implied fallback."""
    await _seed_model_version(pg_pool, trained_model_on_disk)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    # external_ingestor=None → only A0 vectors, no A2
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()

    captured: dict = {}
    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        captured["bundle"] = bundle
        captured["fv_ids"] = fv_ids
    await collector.run_poll_cycle(on_snapshot=on_snap)

    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

    from betfair_trading.models.market import Runner
    runners = [
        Runner(runner_id=101, runner_name="A", sort_priority=1),
        Runner(runner_id=102, runner_name="Draw", sort_priority=2),
        Runner(runner_id=103, runner_name="B", sort_priority=3),
    ]

    probs, inference_id = await provider.get_probabilities(
        captured["bundle"], runners, captured["fv_ids"]
    )

    assert inference_id is None
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM model_inferences")
    assert count == 0


async def test_decision_links_inference_id(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    """Full pipeline: decision row has inference_id linked to a model_inferences row."""
    await _seed_model_version(pg_pool, trained_model_on_disk)

    from betfair_trading.elo.engine import EloEngine
    from betfair_trading.elo.form import FormCalculator
    from betfair_trading.entity_resolution.matcher import TeamMatcher
    from betfair_trading.services.external_ingestor import ExternalDataIngestor

    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n"Arsenal":\n  - "AFC"\n')
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher(mapping_yaml)
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)
    ingestor._loaded = True

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()
    engine = DecisionEngine(
        pool=pg_pool, provider=provider,
        edge_threshold=-1.0,  # allow anything: we're testing wiring, not edge gating
        min_liquidity=0.0, max_spread=999.0,
        max_positions_per_event=999,
    )
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    async def on_snap(bundle, snapshot_ids):
        fv_ids = await fb.on_market_snapshot(bundle, snapshot_ids)
        if fv_ids:
            await engine.evaluate(bundle, snapshot_ids, fv_ids)

    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=on_snap)

    async with pg_pool.acquire() as conn:
        decision_inf = await conn.fetchval(
            "SELECT inference_id FROM decisions WHERE market_id = '1.A'"
        )
    assert decision_inf is not None

    async with pg_pool.acquire() as conn:
        mi_exists = await conn.fetchval(
            "SELECT 1 FROM model_inferences WHERE inference_id = $1", decision_inf
        )
    assert mi_exists == 1


# ---------------------------------------------------------------------------
# Local helper for the no-model fallback test (avoids real DB queries)
# ---------------------------------------------------------------------------

def _make_bundle_and_runners_for_test():
    from decimal import Decimal

    from betfair_trading.models.market import (
        MarketSnapshotBundle,
        Runner,
        RunnerSnapshot,
    )

    bundle = MarketSnapshotBundle(
        market_id="1.A", event_id="E-A",
        snapshot_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
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
```

- [ ] **Step 2: Run tests (must fail)**

Run: `uv run pytest tests/integration/test_model_inference_provider.py -v -m integration`
Expected: 5 FAIL with `ModuleNotFoundError` for `model_inference_provider`.

- [ ] **Step 3: Create `services/model_inference_provider.py`**

Create `src/betfair_trading/services/model_inference_provider.py`:

```python
"""Real probability provider: loads the latest model_version + joblib artifact,
predicts on the A2 feature_vector, persists model_inferences, returns
(probabilities, inference_id).
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import joblib
import structlog

from betfair_trading.db.writer import insert_model_inference
from betfair_trading.models.inference import ModelInference
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.probability_providers import MarketImpliedProvider
from betfair_trading.training.features import (
    build_feature_dict,
    feature_dict_to_array,
)

log = structlog.get_logger()


def _extract_values_from_a2(a2: dict) -> dict[str, float | None]:
    """Map the A2 feature_vector JSONB to training-shape values dict."""
    fh5 = a2.get("form_home_5") or {}
    fa5 = a2.get("form_away_5") or {}
    fh10 = a2.get("form_home_10") or {}
    fa10 = a2.get("form_away_10") or {}
    return {
        "elo_home": a2.get("elo_home"),
        "elo_away": a2.get("elo_away"),
        "elo_delta": a2.get("elo_delta"),
        "form_home_5_ppm": fh5.get("points_per_match"),
        "form_away_5_ppm": fa5.get("points_per_match"),
        "form_home_5_gd": fh5.get("goal_diff_per_match"),
        "form_away_5_gd": fa5.get("goal_diff_per_match"),
        "form_home_5_wr": fh5.get("win_rate"),
        "form_away_5_wr": fa5.get("win_rate"),
        "form_home_10_ppm": fh10.get("points_per_match"),
        "form_away_10_ppm": fa10.get("points_per_match"),
        "form_home_10_gd": fh10.get("goal_diff_per_match"),
        "form_away_10_gd": fa10.get("goal_diff_per_match"),
        "form_home_10_wr": fh10.get("win_rate"),
        "form_away_10_wr": fa10.get("win_rate"),
    }


class ModelInferenceProvider:
    """Loads the latest model at startup. On miss or missing A2 feature, falls back
    to MarketImpliedProvider (zero edge) with a warning log."""

    def __init__(self, pool: asyncpg.Pool, models_dir: str | Path = "models/"):
        self._pool = pool
        self._models_dir = Path(models_dir)
        self._model = None
        self._model_version_id: uuid.UUID | None = None
        self._model_name: str = "STUB_NO_MODEL"
        self._fallback = MarketImpliedProvider()

    @property
    def model_version(self) -> str:
        return self._model_name

    async def initialize(self) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT model_version_id, model_name, file_path "
                "FROM model_versions ORDER BY created_ts DESC LIMIT 1"
            )
        if row is None:
            log.warning("model_inference_no_version_available")
            return

        # The file_path in DB may be absolute or relative; try both.
        candidate = Path(row["file_path"])
        if not candidate.is_absolute() and not candidate.exists():
            candidate = self._models_dir / candidate.name
        if not candidate.exists():
            log.error(
                "model_inference_artifact_missing",
                file_path=str(row["file_path"]),
            )
            return

        self._model = joblib.load(candidate)
        self._model_version_id = row["model_version_id"]
        self._model_name = row["model_name"]
        log.info("model_inference_loaded", version=self._model_name)

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], uuid.UUID | None]:
        if self._model is None:
            probs, _ = await self._fallback.get_probabilities(
                bundle, runners, feature_vector_ids
            )
            return probs, None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT features FROM feature_vectors "
                "WHERE feature_vector_id = ANY($1) "
                "AND feature_set_version = 'A2' LIMIT 1",
                feature_vector_ids,
            )
            if row is None:
                log.warning(
                    "model_inference_no_a2_falling_back",
                    market_id=bundle.market_id,
                )
                probs, _ = await self._fallback.get_probabilities(
                    bundle, runners, feature_vector_ids
                )
                return probs, None

            features_raw = row["features"]
            a2_features = (
                json.loads(features_raw) if isinstance(features_raw, str) else features_raw
            )
            values = _extract_values_from_a2(a2_features)
            feature_dict = build_feature_dict(values)
            X = feature_dict_to_array(feature_dict)

            proba = self._model.predict_proba(X)[0]  # [p_home, p_draw, p_away]

            sorted_r = sorted(
                runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
            )
            result_probs = {
                sorted_r[0].runner_id: float(proba[0]),
                sorted_r[1].runner_id: float(proba[1]),
                sorted_r[2].runner_id: float(proba[2]),
            }

            inference = ModelInference(
                model_version_id=self._model_version_id,
                market_id=bundle.market_id,
                event_id=bundle.event_id,
                asof_ts=bundle.snapshot_ts,
                p_home=Decimal(str(round(float(proba[0]), 6))),
                p_draw=Decimal(str(round(float(proba[1]), 6))),
                p_away=Decimal(str(round(float(proba[2]), 6))),
                feature_vector_ids=feature_vector_ids,
                features_used=feature_dict,
            )
            inference_id = await insert_model_inference(conn, inference)

        return result_probs, inference_id
```

- [ ] **Step 4: Wire into `main.py`**

Open `src/betfair_trading/main.py`. Replace the existing line:

```python
from betfair_trading.services.probability_providers import BiasedStubProvider
```

with:

```python
from betfair_trading.services.model_inference_provider import ModelInferenceProvider
```

Find:

```python
    provider = BiasedStubProvider(home_bias=0.05)
```

Replace with:

```python
    provider = ModelInferenceProvider(pool=pool, models_dir="models/")
    await provider.initialize()
```

- [ ] **Step 5: Run new integration tests**

Run: `uv run pytest tests/integration/test_model_inference_provider.py -v -m integration`
Expected: 5 PASS. Note the `test_get_probabilities_persists_inference` and `test_decision_links_inference_id` tests train a trivial model inside the test (~1-2s each) — total test file runtime ~10-15s.

- [ ] **Step 6: No regressions**

Run: `uv run pytest -v`
Expected: 76 unit + 42 integration = 118 tests pass.

If `test_pipeline_decision.py` tests fail because they rely on `BiasedStubProvider` (which we did NOT remove — only swapped in main.py), they should still pass — these tests construct their own provider explicitly. Verify.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/betfair_trading/services/model_inference_provider.py tests/integration/test_model_inference_provider.py src/betfair_trading/main.py
git commit -m "feat(decision): add ModelInferenceProvider with joblib loading + inference persist; wire main.py"
```

If ruff applied formatting changes, commit separately:

```bash
git add -A
git commit -m "chore: ruff format"
```

---

## Task 10: Final verification + push

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

Run: `uv run pytest -v`
Expected: 118 tests pass, integration suite under 20s (training tests add overhead).

- [ ] **Step 2: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

Run: `uv run ruff format --check src/ tests/`
Expected: idempotent.

- [ ] **Step 3: Update CLAUDE.md**

Modify `CLAUDE.md`. Find:

```
**Currently implemented (Phases 1-2):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates (with stub probability provider).

**Not yet implemented:** Model Inference (real supervised), Execution Engine, P&L Engine, Kafka messaging.
```

Replace with:

```
**Currently implemented (Phases 1-2):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates, Model Inference baseline (LogReg + Platt calibration on Elo+form features).

**Not yet implemented:** Execution Engine, P&L Engine, Kafka messaging.
```

Also add a new command under "Development Commands", before the "Run migrations" line:

```bash
# Train the baseline model from a results CSV
uv run python -m betfair_trading.training.train \
    --csv-path data/results.csv \
    --model-name logistic_v1 \
    --output-dir models/
```

- [ ] **Step 4: Commit CLAUDE.md update**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Model Inference baseline"
```

- [ ] **Step 5: Push the feature branch**

```bash
git push -u origin feature/model-inference
```

Capture the GitHub PR URL.

- [ ] **Step 6: Stop here**

Report the PR URL + final `git log --oneline bdbe465..HEAD` (or current main SHA).

---

## Note finali

- **Eviction cache `_runner_meta_cache` in DecisionEngine**: pre-esistente, non toccato in questo plan. Si applicano gli stessi limiti documentati nel plan precedente (~100 markets, OK per Phase 2).
- **Model loaded at boot only**: il ModelInferenceProvider non ricarica il modello a runtime. Per swap di modello: redeploy o riavvio. Hot reload è un follow-up.
- **Fallback graceful**: se manca il modello o l'A2 feature_vector, il sistema continua a funzionare (decisioni BLOCK_SOFT su edge_threshold). Nessun crash.
- **Bug nel codice di produzione scoperti durante TDD**: NON fixare in questo plan. Aprire follow-up plan separato.

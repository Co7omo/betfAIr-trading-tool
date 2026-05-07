# Phase 1 Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere una suite di integration test end-to-end che esercita la pipeline dati Phase 1 (MarketCollector → ExternalDataIngestor → FeatureBuilder → DB) usando un Postgres effimero e un fake Betfair client.

**Architecture:** Container Postgres effimero session-scoped via `testcontainers-python`, migrazioni Alembic applicate una volta, isolamento per-test via `TRUNCATE ... CASCADE` autouse. Il client Betfair è sostituito da `FakeAsyncBetfairClient` in-process che restituisce `MarketCatalogue` e `MarketSnapshotBundle` (Pydantic) con payload deterministici.

**Tech Stack:** pytest, pytest-asyncio, testcontainers[postgres], asyncpg, alembic, psycopg2-binary (sync driver per Alembic), Pydantic v2.

**Reference spec:** `docs/superpowers/specs/2026-05-07-integration-tests-design.md`

---

## File Structure

Tutti i file nuovi sono sotto `tests/integration/` e `pyproject.toml` viene esteso. Nessun file di produzione viene modificato.

```
tests/
└── integration/
    ├── __init__.py                       # marker pacchetto
    ├── conftest.py                       # fixtures Postgres + integration marker
    ├── fakes/
    │   ├── __init__.py
    │   ├── fake_betfair_client.py        # FakeAsyncBetfairClient
    │   └── fixtures.py                   # make_market, make_book builders
    ├── test_pg_smoke.py                  # smoke test fixture DB
    ├── test_pipeline_collector.py
    ├── test_pipeline_feature_builder.py
    ├── test_pipeline_external_data.py
    └── test_pipeline_edge_cases.py
```

`pyproject.toml`: aggiungere 2 dev deps + sezione `markers`.

---

## Task 1: Setup deps, markers, struttura directory

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/fakes/__init__.py`

- [ ] **Step 1: Aggiungere dev deps a `pyproject.toml`**

Modificare la sezione `[project.optional-dependencies]` da:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "ruff>=0.8.0",
]
```

a:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "ruff>=0.8.0",
    "testcontainers[postgres]>=4.8.0",
    "psycopg2-binary>=2.9.0",
]
```

Nota: `psycopg2-binary` serve perché `alembic/env.py` usa `sqlalchemy.create_engine("postgresql://...")` (driver sync di default = psycopg2). Senza, le migrazioni Alembic falliscono.

- [ ] **Step 2: Aggiungere marker `integration` a `pyproject.toml`**

Modificare la sezione `[tool.pytest.ini_options]` da:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]
```

a:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]
markers = [
    "integration: requires Postgres testcontainer (Docker daemon needed)",
]
```

- [ ] **Step 3: Sincronizzare deps**

Run: `uv sync --all-extras`
Expected: installa `testcontainers`, `psycopg2-binary`, `docker` (transitiva) senza errori.

- [ ] **Step 4: Creare i `__init__.py` vuoti**

Create `tests/integration/__init__.py` con contenuto vuoto.
Create `tests/integration/fakes/__init__.py` con contenuto vuoto.

- [ ] **Step 5: Verificare che gli unit test esistenti non si siano rotti**

Run: `uv run pytest tests/unit -v`
Expected: PASS (tutti i test unit esistenti).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/integration/__init__.py tests/integration/fakes/__init__.py
git commit -m "test: scaffold integration test infrastructure (deps + marker + dirs)"
```

---

## Task 2: Integration conftest con fixture Postgres

**Files:**
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_pg_smoke.py`

- [ ] **Step 1: Scrivere `tests/integration/conftest.py`**

Create `tests/integration/conftest.py` with:

```python
"""Fixtures per integration test: Postgres effimero + isolamento per-test.

Tutti i test sotto tests/integration/ sono marcati `integration` (autouse).
Richiede Docker daemon attivo per testcontainers.
"""

import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

# Marca tutti i moduli sotto tests/integration/ come `integration`
pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """Avvia un container Postgres 16 effimero per la sessione di test."""
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    """URL Postgres sync (psycopg2) per Alembic."""
    return pg_container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="session")
def asyncpg_url(database_url: str) -> str:
    """URL Postgres asyncpg-style per il pool applicativo."""
    return database_url.replace("postgresql://", "postgresql://")


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    """Applica le migrazioni Alembic al container. Eseguita una volta per session."""
    os.environ["DATABASE_URL"] = database_url
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    return database_url


@pytest_asyncio.fixture(scope="session")
async def pg_pool(migrated_db: str) -> AsyncIterator[asyncpg.Pool]:
    """Pool asyncpg session-scoped (riusato come in produzione)."""
    pool = await asyncpg.create_pool(migrated_db, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(pg_pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Pulisce tutte le tabelle append-only prima di ogni test."""
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE markets, runners, market_snapshots, "
            "external_feature_snapshots, feature_vectors, config_snapshots "
            "RESTART IDENTITY CASCADE"
        )
    yield
```

- [ ] **Step 2: Scrivere `tests/integration/test_pg_smoke.py`**

Create `tests/integration/test_pg_smoke.py` with:

```python
"""Smoke test: il container Postgres parte e le migrazioni si applicano."""

import asyncpg


async def test_pool_can_query(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


async def test_schema_tables_exist(pg_pool: asyncpg.Pool):
    expected = {
        "markets",
        "runners",
        "market_snapshots",
        "external_feature_snapshots",
        "feature_vectors",
        "config_snapshots",
    }
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    found = {r["tablename"] for r in rows}
    assert expected.issubset(found), f"missing: {expected - found}"


async def test_truncate_isolation(pg_pool: asyncpg.Pool):
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM markets")
    assert count == 0
```

- [ ] **Step 3: Lanciare lo smoke test**

Run: `uv run pytest tests/integration/test_pg_smoke.py -v -m integration`
Expected: 3 test PASS. Primo run lento (~5s startup container), successivi <1s/test.

Se fallisce con `docker.errors.DockerException`: il Docker daemon non è attivo. Avviare Docker Desktop e ripetere.

- [ ] **Step 4: Verificare che default run ESCLUDA integration**

Run: `uv run pytest -v -m "not integration"`
Expected: gira solo gli unit test esistenti, NON tocca i test in `tests/integration/` (skipped o non collected). Nessun container avviato.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_pg_smoke.py
git commit -m "test: add Postgres testcontainer fixtures + schema smoke test"
```

---

## Task 3: FakeAsyncBetfairClient + fixture builders

**Files:**
- Create: `tests/integration/fakes/fake_betfair_client.py`
- Create: `tests/integration/fakes/fixtures.py`
- Create: `tests/integration/fakes/test_fakes_smoke.py`

- [ ] **Step 1: Scrivere `tests/integration/fakes/fake_betfair_client.py`**

Create with:

```python
"""Fake AsyncBetfairClient per integration test.

Restituisce direttamente Pydantic models (MarketCatalogue, MarketSnapshotBundle)
saltando la conversione `from_betfair`. Il MarketCollector consuma questi due
metodi: list_market_catalogue() e list_market_book(market_ids).
"""

from betfair_trading.models.market import MarketCatalogue, MarketSnapshotBundle


class FakeAsyncBetfairClient:
    def __init__(self):
        self._catalogue: list[MarketCatalogue] = []
        self._book_queues: dict[str, list[MarketSnapshotBundle]] = {}
        self._book_call_count: dict[str, int] = {}

    # Builder API per i test ---------------------------------------------------

    def add_market(self, catalogue: MarketCatalogue) -> None:
        """Aggiungi un MarketCatalogue al risultato di list_market_catalogue()."""
        self._catalogue.append(catalogue)

    def queue_book(self, market_id: str, bundle: MarketSnapshotBundle) -> None:
        """Accoda un MarketSnapshotBundle che sarà restituito al prossimo
        list_market_book() per questo market_id (round-robin sulla coda)."""
        self._book_queues.setdefault(market_id, []).append(bundle)

    # Surface client ----------------------------------------------------------

    async def list_market_catalogue(
        self, hours_ahead: float = 2.5, max_results: int = 100
    ) -> list[MarketCatalogue]:
        return list(self._catalogue)

    async def list_market_book(
        self, market_ids: list[str]
    ) -> list[MarketSnapshotBundle]:
        out: list[MarketSnapshotBundle] = []
        for mid in market_ids:
            queue = self._book_queues.get(mid, [])
            if not queue:
                continue
            idx = self._book_call_count.get(mid, 0) % len(queue)
            out.append(queue[idx])
            self._book_call_count[mid] = self._book_call_count.get(mid, 0) + 1
        return out
```

- [ ] **Step 2: Scrivere `tests/integration/fakes/fixtures.py`**

Create with:

```python
"""Builder helper per costruire MarketCatalogue / MarketSnapshotBundle nei test."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from betfair_trading.models.market import (
    MarketCatalogue,
    MarketSnapshotBundle,
    Runner,
    RunnerSnapshot,
)


def make_market(
    market_id: str = "1.100",
    event_id: str = "EVT-1",
    home: str = "Manchester United",
    away: str = "Chelsea",
    competition: str = "EPL",
    start_time: datetime | None = None,
    runner_ids: tuple[int, int, int] = (101, 102, 103),
) -> MarketCatalogue:
    """MarketCatalogue 1X2 con 3 runner: home, draw, away."""
    if start_time is None:
        start_time = datetime.now(UTC) + timedelta(minutes=60)

    return MarketCatalogue(
        market_id=market_id,
        event_id=event_id,
        event_name=f"{home} v {away}",
        competition_id="C1",
        competition_name=competition,
        country_code="GB",
        start_time=start_time,
        runners=[
            Runner(runner_id=runner_ids[0], runner_name=home, sort_priority=1),
            Runner(runner_id=runner_ids[1], runner_name="The Draw", sort_priority=2),
            Runner(runner_id=runner_ids[2], runner_name=away, sort_priority=3),
        ],
    )


def make_book(
    market_id: str = "1.100",
    event_id: str = "EVT-1",
    runner_quotes: list[tuple[int, float, float, float, float]] | None = None,
    status: str = "OPEN",
    inplay: bool = False,
    total_matched: float = 1000.0,
    snapshot_ts: datetime | None = None,
    minutes_to_start: float = 60.0,
) -> MarketSnapshotBundle:
    """MarketSnapshotBundle. runner_quotes = [(runner_id, back, lay, size_back, size_lay), ...]."""
    if runner_quotes is None:
        runner_quotes = [
            (101, 2.0, 2.04, 500.0, 500.0),
            (102, 3.5, 3.6, 200.0, 200.0),
            (103, 4.0, 4.1, 300.0, 300.0),
        ]
    if snapshot_ts is None:
        snapshot_ts = datetime.now(UTC)

    runners = []
    for rid, back, lay, sb, sl in runner_quotes:
        runners.append(
            RunnerSnapshot(
                runner_id=rid,
                best_back_price=Decimal(str(back)),
                best_back_size=Decimal(str(sb)),
                best_lay_price=Decimal(str(lay)),
                best_lay_size=Decimal(str(sl)),
                spread=Decimal(str(round(lay - back, 4))),
                traded_volume=Decimal("100.0"),
            )
        )

    return MarketSnapshotBundle(
        market_id=market_id,
        event_id=event_id,
        snapshot_ts=snapshot_ts,
        runners=runners,
        market_status=status,
        inplay=inplay,
        total_matched=Decimal(str(total_matched)),
        minutes_to_start=minutes_to_start,
    )
```

- [ ] **Step 3: Scrivere smoke test `tests/integration/fakes/test_fakes_smoke.py`**

Create with:

```python
"""Smoke test del fake client e dei builder."""

from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_fake_returns_added_catalogue():
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.add_market(make_market(market_id="1.B"))

    result = await fake.list_market_catalogue()

    assert len(result) == 2
    assert {m.market_id for m in result} == {"1.A", "1.B"}


async def test_fake_book_round_robin():
    fake = FakeAsyncBetfairClient()
    fake.queue_book("1.A", make_book(market_id="1.A", total_matched=10.0))
    fake.queue_book("1.A", make_book(market_id="1.A", total_matched=20.0))

    first = await fake.list_market_book(["1.A"])
    second = await fake.list_market_book(["1.A"])
    third = await fake.list_market_book(["1.A"])

    assert len(first) == 1 and float(first[0].total_matched) == 10.0
    assert len(second) == 1 and float(second[0].total_matched) == 20.0
    # round-robin: terzo torna alla prima
    assert len(third) == 1 and float(third[0].total_matched) == 10.0


async def test_fake_book_skips_unqueued_market():
    fake = FakeAsyncBetfairClient()
    fake.queue_book("1.A", make_book(market_id="1.A"))

    result = await fake.list_market_book(["1.A", "1.B"])

    assert len(result) == 1
    assert result[0].market_id == "1.A"
```

Nota: questi test non usano il container Postgres ma sono sotto `tests/integration/`, quindi sono marcati `integration` (eseguiti solo con `-m integration`). Ok perché logicamente fanno parte del setup integration.

- [ ] **Step 4: Lanciare gli smoke test**

Run: `uv run pytest tests/integration/fakes/test_fakes_smoke.py -v -m integration`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/fakes/
git commit -m "test: add FakeAsyncBetfairClient and Pydantic builder helpers"
```

---

## Task 4: Pipeline Collector — discovery + polling

**Files:**
- Create: `tests/integration/test_pipeline_collector.py`

- [ ] **Step 1: Scrivere `test_pipeline_collector.py`**

Create with:

```python
"""End-to-end: MarketCollector.run_discovery() + run_poll_cycle() persistono su Postgres."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from betfair_trading.services.market_collector import MarketCollector
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_discovery_persists_markets_and_runners(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A", home="Liverpool", away="Arsenal"))
    fake.add_market(make_market(market_id="1.B", event_id="E-B", home="Chelsea", away="Spurs"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)

    new_count = await collector.run_discovery()

    assert new_count == 2
    async with pg_pool.acquire() as conn:
        markets = await conn.fetch("SELECT market_id, event_id FROM markets ORDER BY market_id")
        runners = await conn.fetch("SELECT market_id, runner_id FROM runners ORDER BY market_id, runner_id")

    assert [m["market_id"] for m in markets] == ["1.A", "1.B"]
    assert {(m["market_id"], m["event_id"]) for m in markets} == {("1.A", "E-A"), ("1.B", "E-B")}
    assert len(runners) == 6  # 3 runner per market * 2 market


async def test_polling_persists_snapshots(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", event_id="E-A", start_time=start_time))

    # Tre book diversi nella coda → tre poll cycle li consumano in ordine
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.0, 2.04, 500, 500),
                                                    (102, 3.5, 3.6, 200, 200),
                                                    (103, 4.0, 4.1, 300, 300)],
                                     total_matched=1000.0))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.10, 2.14, 500, 500),
                                                    (102, 3.40, 3.50, 200, 200),
                                                    (103, 3.95, 4.05, 300, 300)],
                                     total_matched=1500.0))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A",
                                     runner_quotes=[(101, 2.20, 2.24, 500, 500),
                                                    (102, 3.30, 3.40, 200, 200),
                                                    (103, 3.90, 4.00, 300, 300)],
                                     total_matched=2000.0))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()

    n1 = await collector.run_poll_cycle()
    n2 = await collector.run_poll_cycle()
    n3 = await collector.run_poll_cycle()

    assert n1 == n2 == n3 == 3  # 3 runner per cycle

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT runner_id, best_back_price, total_matched FROM market_snapshots "
            "WHERE market_id = '1.A' ORDER BY snapshot_ts, runner_id"
        )
    assert len(rows) == 9
    # Le total_matched osservate sul home runner (101) nei 3 cycle
    home_totals = [
        r["total_matched"] for r in rows if r["runner_id"] == 101
    ]
    assert home_totals == [Decimal("1000.0"), Decimal("1500.0"), Decimal("2000.0")]


async def test_minutes_to_start_persisted(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A", minutes_to_start=60.0))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()
    await collector.run_poll_cycle()

    async with pg_pool.acquire() as conn:
        m2s = await conn.fetchval(
            "SELECT minutes_to_start FROM market_snapshots WHERE market_id = '1.A' LIMIT 1"
        )
    assert m2s is not None
    # Il bundle ha minutes_to_start=60.0 hardcoded → DB Decimal(8,2)
    assert abs(float(m2s) - 60.0) < 0.01
```

- [ ] **Step 2: Lanciare i test**

Run: `uv run pytest tests/integration/test_pipeline_collector.py -v -m integration`
Expected: 3 PASS.

Se un test fallisce con un'asserzione DB:
- Probabile drift fra builder e schema (es. `total_matched` arriva NULL invece che Decimal). Leggere l'error message, capire se il bug è nel codice di produzione o nel test.
- Se è un bug di produzione: NON fixarlo qui. Fare commit del test che fallisce con `xfail` annotato e creare follow-up. Annotare nel commit message.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pipeline_collector.py
git commit -m "test: integration tests for MarketCollector discovery + polling"
```

---

## Task 5: Pipeline Feature Builder — A0 + persistenza + hash

**Files:**
- Create: `tests/integration/test_pipeline_feature_builder.py`

- [ ] **Step 1: Scrivere `test_pipeline_feature_builder.py`**

Create with:

```python
"""End-to-end: snapshot → FeatureBuilder.on_market_snapshot → feature_vectors persistiti."""

from datetime import UTC, datetime, timedelta

import asyncpg

from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_a0_feature_vector_written(pg_pool: asyncpg.Pool):
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", event_id="E-A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT runner_id, feature_set_version, snapshot_id, ext_snapshot_id, features "
            "FROM feature_vectors WHERE market_id = '1.A' ORDER BY runner_id"
        )

    assert len(rows) == 3  # 3 runner
    versions = {r["feature_set_version"] for r in rows}
    assert versions == {"A0"}
    assert all(r["snapshot_id"] is not None for r in rows)
    assert all(r["ext_snapshot_id"] is None for r in rows)


async def test_feature_hash_deterministic(pg_pool: asyncpg.Pool):
    """Stesso bundle in input → stesso feature_hash (SHA256 di canonical JSON)."""
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))  # IDENTICO al primo

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        # feature_hash è computed_field nel modello Pydantic, NON una colonna persistita
        # da insert_feature_vector — ricalcoliamolo dal payload features
        rows = await conn.fetch(
            "SELECT runner_id, features FROM feature_vectors "
            "WHERE market_id = '1.A' AND runner_id = 101 ORDER BY generated_at"
        )

    assert len(rows) == 2
    # Le features dovrebbero essere strutturalmente identiche fra i due cycle
    import json
    f1 = json.loads(rows[0]["features"]) if isinstance(rows[0]["features"], str) else rows[0]["features"]
    f2 = json.loads(rows[1]["features"]) if isinstance(rows[1]["features"], str) else rows[1]["features"]
    # `minutes_to_start` è hardcoded in make_book → identico
    assert f1 == f2


async def test_feature_vector_links_correct_snapshot(pg_pool: asyncpg.Pool):
    """Ogni feature_vector deve puntare al proprio snapshot_id."""
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT fv.runner_id, fv.snapshot_id, ms.runner_id AS ms_runner "
            "FROM feature_vectors fv "
            "JOIN market_snapshots ms ON ms.snapshot_id = fv.snapshot_id "
            "WHERE fv.market_id = '1.A' ORDER BY fv.runner_id"
        )
    assert len(rows) == 3
    for r in rows:
        assert r["runner_id"] == r["ms_runner"]
```

- [ ] **Step 2: Lanciare i test**

Run: `uv run pytest tests/integration/test_pipeline_feature_builder.py -v -m integration`
Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pipeline_feature_builder.py
git commit -m "test: integration tests for FeatureBuilder A0 + snapshot linkage"
```

---

## Task 6: Pipeline External Data — Elo + form as-of + persistenza

**Files:**
- Create: `tests/integration/test_pipeline_external_data.py`

- [ ] **Step 1: Scrivere `test_pipeline_external_data.py`**

Create with:

```python
"""End-to-end: ExternalDataIngestor → Elo/form as-of → external_feature_snapshots."""

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.db.writer import insert_external_feature_snapshot
from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor


@pytest.fixture
def results_csv(tmp_path: Path) -> Path:
    """4 match Liverpool vs Arsenal: -30g, -10g, +5g, +10g rispetto a 2026-04-01."""
    p = tmp_path / "results.csv"
    rows = [
        ("01/03/2026", "Liverpool", "Arsenal", "H", 2, 0),  # d-30
        ("22/03/2026", "Liverpool", "Arsenal", "D", 1, 1),  # d-10
        ("06/04/2026", "Liverpool", "Arsenal", "A", 0, 1),  # d+5  (futuro per asof=2026-04-01)
        ("11/04/2026", "Arsenal", "Liverpool", "H", 3, 0),  # d+10 (futuro)
    ]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for r in rows:
            w.writerow(r)
    return p


async def test_load_historical_results_populates_elo_form(
    pg_pool: asyncpg.Pool, results_csv: Path
):
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher()  # nessun mappings file → no aliases, ma "Liverpool"/"Arsenal" sono canonici
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)

    n = await ingestor.load_historical_results(results_csv)

    assert n == 4
    # Dopo 4 match, Elo non è più ai default
    assert elo.history_size == 4
    assert elo.get_rating("Liverpool") != 1500.0
    assert elo.get_rating("Arsenal") != 1500.0


async def test_asof_excludes_future_matches(
    pg_pool: asyncpg.Pool, results_csv: Path
):
    """Anti-leakage: asof_ts=2026-04-01 NON deve riflettere match d+5 e d+10."""
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)

    await ingestor.load_historical_results(results_csv)

    asof = datetime(2026, 4, 1, tzinfo=UTC)

    # Calcolo manuale atteso: dopo i primi 2 match
    # Match 1 (d-30): Liverpool batte Arsenal a casa
    #   exp_home = 1/(1+10^0) = 0.5; new_home = 1500 + 20*(1-0.5) = 1510
    #   new_away = 1500 + 20*(0-0.5) = 1490
    # Match 2 (d-10): Liverpool 1-1 Arsenal a casa
    #   delta = 1490-1510 = -20; exp_home = 1/(1+10^(-20/400)) ≈ 0.5288
    #   new_home = 1510 + 20*(0.5-0.5288) = 1509.42 (~)
    #   new_away = 1490 + 20*(0.5-0.4712) = 1490.57 (~)

    elo_home, elo_away = elo.get_ratings_asof("Liverpool", "Arsenal", asof)
    assert 1508.0 < elo_home < 1511.0, f"unexpected elo_home={elo_home}"
    assert 1489.0 < elo_away < 1492.0, f"unexpected elo_away={elo_away}"

    # Form Liverpool ai primi 5 match disponibili prima di asof = 2 match (W, D)
    f5_lpool = form.compute_form("Liverpool", asof, n=5)
    assert f5_lpool is not None
    assert f5_lpool.points_per_match == pytest.approx((3 + 1) / 2)  # W=3, D=1
    assert f5_lpool.win_rate == pytest.approx(0.5)


async def test_external_snapshot_persisted(pg_pool: asyncpg.Pool, results_csv: Path):
    """ExternalDataIngestor.get_features_asof() + insert_external_feature_snapshot
    produce una riga in external_feature_snapshots.
    """
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)
    await ingestor.load_historical_results(results_csv)

    asof = datetime(2026, 4, 1, tzinfo=UTC)
    bundle = ingestor.get_features_asof("Liverpool", "Arsenal", asof, market_id="1.A")
    assert bundle is not None

    async with pg_pool.acquire() as conn:
        ext_id = await insert_external_feature_snapshot(conn, bundle)

    assert ext_id is not None

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT home_team, away_team, elo_home, elo_away, elo_delta, "
            "form_home_5, form_away_5, match_confidence, quality_flags "
            "FROM external_feature_snapshots WHERE ext_snapshot_id = $1",
            ext_id,
        )

    assert row["home_team"] == "Liverpool"
    assert row["away_team"] == "Arsenal"
    assert row["elo_home"] is not None and row["elo_away"] is not None
    assert row["elo_delta"] == row["elo_home"] - row["elo_away"]
    assert row["form_home_5"] is not None  # 2 match disponibili pre-asof
    assert row["match_confidence"] == "HIGH"  # confidence=1.0 (esatto sui canonici)
    flags = row["quality_flags"]
    if isinstance(flags, str):
        flags = json.loads(flags)
    assert flags.get("home_confidence") == 1.0
    assert flags.get("away_confidence") == 1.0
```

- [ ] **Step 2: Lanciare i test**

Run: `uv run pytest tests/integration/test_pipeline_external_data.py -v -m integration`
Expected: 3 PASS.

Note di troubleshooting:
- Se `test_external_snapshot_persisted` fallisce su `quality_flags` con `TypeError`: il driver asyncpg restituisce JSONB già come dict, non come stringa. Il test gestisce entrambi. Se fallisce su `home_confidence`, leggere il valore esatto e capire se la docstring di `TeamMatcher.resolve` mente sui valori (ritorna 1.0 o 0.0).
- Se il calcolo Elo manuale è troppo stretto: allargare la tolleranza nel range, ma documentare il valore osservato nel commit.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pipeline_external_data.py
git commit -m "test: integration tests for Elo/form as-of + external snapshot persist"
```

---

## Task 7: Pipeline Edge Cases — window, suspended, entity miss

**Files:**
- Create: `tests/integration/test_pipeline_edge_cases.py`

- [ ] **Step 1: Scrivere `test_pipeline_edge_cases.py`**

Create with:

```python
"""Edge case end-to-end: fuori finestra, market sospeso, entity match fallito."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from betfair_trading.db.writer import insert_external_feature_snapshot
from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


async def test_market_outside_window_skipped(pg_pool: asyncpg.Pool):
    """Market con start_time fuori finestra T-120/T-10 → discoverato ma non polled."""
    fake = FakeAsyncBetfairClient()
    now = datetime.now(UTC)

    # In window (T-60min)
    fake.add_market(make_market(market_id="1.IN", start_time=now + timedelta(minutes=60)))
    fake.queue_book("1.IN", make_book(market_id="1.IN"))
    # Oltre window_start (T-200min: troppo lontano nel futuro)
    fake.add_market(make_market(market_id="1.FAR", start_time=now + timedelta(minutes=200)))
    fake.queue_book("1.FAR", make_book(market_id="1.FAR"))
    # Sotto window_end (T-5min: troppo vicino al kick-off)
    fake.add_market(make_market(market_id="1.NEAR", start_time=now + timedelta(minutes=5)))
    fake.queue_book("1.NEAR", make_book(market_id="1.NEAR"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()
    snapshots = await collector.run_poll_cycle()

    # Solo "1.IN" è eligible → 3 runner snapshot
    assert snapshots == 3

    async with pg_pool.acquire() as conn:
        market_ids_with_snapshots = await conn.fetch(
            "SELECT DISTINCT market_id FROM market_snapshots"
        )
    assert {r["market_id"] for r in market_ids_with_snapshots} == {"1.IN"}


async def test_suspended_market_snapshot_recorded(pg_pool: asyncpg.Pool):
    """Market SUSPENDED: snapshot scritto comunque (audit-first), market_status='SUSPENDED'."""
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A", status="SUSPENDED"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()
    n = await collector.run_poll_cycle()

    assert n == 3
    async with pg_pool.acquire() as conn:
        statuses = await conn.fetch(
            "SELECT DISTINCT market_status FROM market_snapshots WHERE market_id = '1.A'"
        )
    assert [r["market_status"] for r in statuses] == ["SUSPENDED"]


async def test_suspended_market_still_builds_features(pg_pool: asyncpg.Pool):
    """Anche su market SUSPENDED, il FeatureBuilder costruisce A0 (audit completo)."""
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A", status="SUSPENDED"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM feature_vectors WHERE market_id = '1.A'"
        )
    assert count == 3


async def test_entity_match_miss_does_not_break_pipeline(pg_pool: asyncpg.Pool, tmp_path: Path):
    """Team unknown a TeamMatcher → match_confidence='LOW' ma external snapshot scritto comunque.
    La pipeline market (snapshot + feature_vector A0) è indipendente e continua.
    """
    # Mappings YAML solo con un team noto, l'altro mancante
    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n')

    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher(mapping_yaml)

    asof = datetime.now(UTC)
    bundle_ext = ExternalDataIngestor(elo, form, matcher, pg_pool).get_features_asof(
        home_team="Liverpool",
        away_team="ZZZ Ignoto FC",  # non in mappings → confidence=0.0 → match_confidence=LOW
        asof_ts=asof,
        market_id="1.A",
    )
    assert bundle_ext is not None
    assert bundle_ext.match_confidence == "LOW"

    async with pg_pool.acquire() as conn:
        ext_id = await insert_external_feature_snapshot(conn, bundle_ext)
    assert ext_id is not None

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT match_confidence, quality_flags FROM external_feature_snapshots "
            "WHERE ext_snapshot_id = $1",
            ext_id,
        )
    assert row["match_confidence"] == "LOW"
    flags = row["quality_flags"]
    if isinstance(flags, str):
        flags = json.loads(flags)
    assert flags["away_confidence"] == 0.0

    # La pipeline market continua normalmente
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        snap_count = await conn.fetchval("SELECT COUNT(*) FROM market_snapshots WHERE market_id='1.A'")
        fv_count = await conn.fetchval("SELECT COUNT(*) FROM feature_vectors WHERE market_id='1.A'")
    assert snap_count == 3
    assert fv_count == 3
```

- [ ] **Step 2: Lanciare i test**

Run: `uv run pytest tests/integration/test_pipeline_edge_cases.py -v -m integration`
Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pipeline_edge_cases.py
git commit -m "test: integration edge cases (window, suspended, entity miss)"
```

---

## Task 8: Verifica suite completa + lint

**Files:** nessuno modificato — solo verifica.

- [ ] **Step 1: Lanciare TUTTA la suite integration**

Run: `uv run pytest -v -m integration`
Expected: tutti i test sopra PASS in <60s totali (incluso boot del container).

- [ ] **Step 2: Lanciare la suite unit (no Docker)**

Run: `uv run pytest -v -m "not integration"`
Expected: tutti gli unit test esistenti PASS, NESSUN container avviato.

- [ ] **Step 3: Lint**

Run: `uv run ruff check tests/integration/`
Expected: nessun errore. Se ci sono violazioni, correggere e ri-eseguire.

Run: `uv run ruff format tests/integration/`
Expected: nessuna modifica residua dopo il primo run.

- [ ] **Step 4: Commit eventuali fix di lint**

Se ruff ha applicato modifiche:

```bash
git add tests/integration/
git commit -m "chore: ruff lint integration tests"
```

Altrimenti saltare.

- [ ] **Step 5: Aggiornare CLAUDE.md con il comando**

Modify: `CLAUDE.md`

Sotto "Development Commands" aggiungere dopo `uv run pytest -v`:

```bash
# Run only unit tests (default fast, no Docker)
uv run pytest -v -m "not integration"

# Run integration tests (requires Docker daemon)
uv run pytest -v -m integration
```

- [ ] **Step 6: Commit aggiornamento CLAUDE.md**

```bash
git add CLAUDE.md
git commit -m "docs: document integration test commands in CLAUDE.md"
```

---

## Note finali

- **Bug nel codice di produzione scoperti durante questi test**: NON fixarli in questo plan. Aprire un follow-up plan separato. Lo scopo qui è solo aggiungere copertura: Phase 1 va consolidata prima di Phase 2.
- **`feature_hash`**: è un `@computed_field` Pydantic, non viene persistito su `feature_vectors` come colonna a sé. Il test `test_feature_hash_deterministic` verifica la determinatezza confrontando il payload `features`, che è equivalente perché l'hash è una funzione pura del payload.
- **CI**: questo plan non configura CI. Un follow-up separato può aggiungere un job GitHub Actions con servizio Docker-in-Docker.

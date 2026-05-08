# A1/A2 Feature Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cablare `ExternalDataIngestor` dentro `FeatureBuilder` per produrre `feature_vector` A1 (market+Elo) e A2 (market+Elo+form) accanto al baseline A0, persistendo automaticamente `external_feature_snapshots`.

**Architecture:** `FeatureBuilder` mantiene tre cache per market (`ext_id`, `ext_bundle`, `runner_meta`). Al primo snapshot di un market, se l'ingestor è disponibile, estrae home/away dai metadata dei runner caricati dalla tabella `runners`, chiama `ingestor.get_features_asof(snapshot_ts)`, persiste un `external_feature_snapshots`, e cacha gli ID. Per ogni runner di ogni snapshot scrive A0 sempre, A1 e A2 se l'ext_snapshot esiste.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, asyncpg, Pydantic v2, testcontainers (integration).

**Reference spec:** `docs/superpowers/specs/2026-05-08-a1-a2-feature-wiring-design.md`

---

## File Structure

Cambiamenti tutti circoscritti a 3 file di produzione + 2 file di test.

```
src/betfair_trading/services/
├── external_ingestor.py    # +1 riga (history_loaded in quality_flags)
└── feature_builder.py      # estensione: caches + 5 nuovi metodi privati

tests/
├── unit/
│   ├── test_external_ingestor.py   # NUOVO (1 test su history_loaded)
│   └── test_feature_builder.py     # +3 test (extract_teams, build_a1, build_a2)
└── integration/
    └── test_pipeline_a1_a2.py      # NUOVO (5 test integration)
```

`main.py` NON cambia (il wiring `FeatureBuilder(pool, ingestor)` esiste già).

---

## Pre-requisiti git

Prima di iniziare le task, l'utente crea il feature branch dal main aggiornato:

```bash
git checkout main
git pull
git checkout -b feature/a1-a2-feature-wiring
```

Tutte le task committano su questo branch.

---

## Task 1: `history_loaded` flag in ExternalDataIngestor.quality_flags

**Files:**
- Create: `tests/unit/test_external_ingestor.py`
- Modify: `src/betfair_trading/services/external_ingestor.py:113-118`

- [ ] **Step 1: Scrivere il test fallente**

Create `tests/unit/test_external_ingestor.py`:

```python
"""Unit tests for ExternalDataIngestor.get_features_asof()."""

from datetime import UTC, datetime

from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor


def test_quality_flags_includes_history_loaded_false_when_not_loaded():
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, db_pool=None)

    bundle = ingestor.get_features_asof(
        home_team="Liverpool", away_team="Arsenal",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC), market_id="1.X",
    )

    assert bundle is not None
    assert bundle.quality_flags["history_loaded"] is False


def test_quality_flags_includes_history_loaded_true_after_load(tmp_path):
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher()
    ingestor = ExternalDataIngestor(elo, form, matcher, db_pool=None)
    # Forza il flag senza dover caricare un file CSV reale
    ingestor._loaded = True

    bundle = ingestor.get_features_asof(
        home_team="Liverpool", away_team="Arsenal",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC), market_id="1.X",
    )

    assert bundle.quality_flags["history_loaded"] is True
```

- [ ] **Step 2: Lanciare i test (devono fallire)**

Run: `uv run pytest tests/unit/test_external_ingestor.py -v -m "not integration"`
Expected: 2 FAILED, key error / assertion error su `quality_flags["history_loaded"]`.

- [ ] **Step 3: Modificare `external_ingestor.py`**

Aprire `src/betfair_trading/services/external_ingestor.py`. Trovare il blocco (intorno alla riga 113):

```python
        quality_flags = {
            "home_confidence": conf_h,
            "away_confidence": conf_a,
            "resolved_home": resolved_home,
            "resolved_away": resolved_away,
        }
```

Sostituire con:

```python
        quality_flags = {
            "home_confidence": conf_h,
            "away_confidence": conf_a,
            "resolved_home": resolved_home,
            "resolved_away": resolved_away,
            "history_loaded": self._loaded,
        }
```

- [ ] **Step 4: Lanciare i test (devono passare)**

Run: `uv run pytest tests/unit/test_external_ingestor.py -v -m "not integration"`
Expected: 2 PASSED.

- [ ] **Step 5: Verificare nessuna regressione**

Run: `uv run pytest -v -m "not integration"`
Expected: tutti gli unit test passano (i 26 esistenti + 2 nuovi = 28).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_external_ingestor.py src/betfair_trading/services/external_ingestor.py
git commit -m "feat(external): add history_loaded to quality_flags"
```

---

## Task 2: Helpers puri in FeatureBuilder (`_extract_teams`, `_build_a1`, `_build_a2`)

**Files:**
- Modify: `tests/unit/test_feature_builder.py` (aggiunge 3 test)
- Modify: `src/betfair_trading/services/feature_builder.py` (aggiunge 3 metodi statici, import)

- [ ] **Step 1: Scrivere i 3 test fallenti**

Aggiungere in fondo a `tests/unit/test_feature_builder.py`:

```python
from betfair_trading.models.external import ExternalFeatureBundle, FormFeatures
from betfair_trading.models.market import Runner


def test_extract_teams_uses_sort_priority():
    runners = [
        Runner(runner_id=2, runner_name="The Draw", sort_priority=2),
        Runner(runner_id=1, runner_name="Liverpool", sort_priority=1),
        Runner(runner_id=3, runner_name="Arsenal", sort_priority=3),
    ]
    home, away = FeatureBuilder._extract_teams(runners)
    assert home == "Liverpool"
    assert away == "Arsenal"


def test_extract_teams_handles_none_sort_priority():
    """Fallback: None sort_priority finisce in coda, non crasha."""
    runners = [
        Runner(runner_id=2, runner_name="Liverpool", sort_priority=1),
        Runner(runner_id=1, runner_name="Mystery", sort_priority=None),
        Runner(runner_id=3, runner_name="Arsenal", sort_priority=3),
    ]
    home, away = FeatureBuilder._extract_teams(runners)
    assert home == "Liverpool"
    assert away == "Arsenal"


def test_build_a1_extends_a0_with_elo_fields():
    a0 = {"best_back": 2.0, "implied_prob_raw": 0.5, "minutes_to_start": 60.0}
    ext = ExternalFeatureBundle(
        event_key="L_vs_A",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC),
        home_team="Liverpool", away_team="Arsenal",
        elo_home=Decimal("1510.50"), elo_away=Decimal("1490.50"),
        elo_delta=Decimal("20.00"),
        match_confidence="HIGH",
    )
    a1 = FeatureBuilder._build_a1(a0, ext)

    # A0 fields preserved
    assert a1["best_back"] == 2.0
    assert a1["minutes_to_start"] == 60.0
    # A1-specific fields added
    assert a1["elo_home"] == 1510.50
    assert a1["elo_away"] == 1490.50
    assert a1["elo_delta"] == 20.00
    assert a1["match_confidence"] == "HIGH"


def test_build_a2_extends_a1_with_form_fields():
    a1 = {
        "best_back": 2.0, "elo_home": 1510.0, "elo_away": 1490.0,
        "elo_delta": 20.0, "match_confidence": "HIGH",
    }
    form_h5 = FormFeatures(
        points_per_match=2.0, goal_diff_per_match=1.5, win_rate=0.6,
        draw_rate=0.2, loss_rate=0.2,
    )
    ext = ExternalFeatureBundle(
        event_key="L_vs_A",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC),
        home_team="Liverpool", away_team="Arsenal",
        form_home_5=form_h5, form_away_5=None,
        form_home_10=None, form_away_10=None,
        match_confidence="HIGH",
    )
    a2 = FeatureBuilder._build_a2(a1, ext)

    # A1 fields preserved
    assert a2["elo_home"] == 1510.0
    # A2-specific fields added
    assert a2["form_home_5"] == {
        "points_per_match": 2.0,
        "goal_diff_per_match": 1.5,
        "win_rate": 0.6,
    }
    assert a2["form_away_5"] is None
    assert a2["form_home_10"] is None
    assert a2["form_away_10"] is None
```

- [ ] **Step 2: Lanciare i test (devono fallire)**

Run: `uv run pytest tests/unit/test_feature_builder.py -v -m "not integration"`
Expected: 4 FAIL — `AttributeError: type object 'FeatureBuilder' has no attribute '_extract_teams'` (e simili per `_build_a1`, `_build_a2`).

- [ ] **Step 3: Implementare i 3 metodi statici**

In `src/betfair_trading/services/feature_builder.py`:

Aggiungere all'inizio dei `from`:

```python
from betfair_trading.models.external import ExternalFeatureBundle
from betfair_trading.models.market import MarketSnapshotBundle, Runner
```

(Sostituire l'import esistente `from betfair_trading.models.market import MarketSnapshotBundle` con la versione che include `Runner`.)

Aggiungere in fondo alla classe `FeatureBuilder` (dopo `_build_a0`):

```python
    @staticmethod
    def _extract_teams(runners: list[Runner]) -> tuple[str, str]:
        """Betfair Match Odds: sort_priority 1=home, 2=draw, 3=away.
        Runners with None sort_priority go last (defensive fallback).
        """
        sorted_runners = sorted(
            runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
        )
        return sorted_runners[0].runner_name, sorted_runners[-1].runner_name

    @staticmethod
    def _build_a1(a0: dict, ext: ExternalFeatureBundle) -> dict:
        """A1 = A0 + Elo fields. Same fields for all runners; runner_id distinguishes."""
        return {
            **a0,
            "elo_home": float(ext.elo_home) if ext.elo_home is not None else None,
            "elo_away": float(ext.elo_away) if ext.elo_away is not None else None,
            "elo_delta": float(ext.elo_delta) if ext.elo_delta is not None else None,
            "match_confidence": ext.match_confidence,
        }

    @staticmethod
    def _build_a2(a1: dict, ext: ExternalFeatureBundle) -> dict:
        """A2 = A1 + form fields (home/away, n=5/10)."""
        def _form_dict(f):
            if f is None:
                return None
            return {
                "points_per_match": f.points_per_match,
                "goal_diff_per_match": f.goal_diff_per_match,
                "win_rate": f.win_rate,
            }
        return {
            **a1,
            "form_home_5":  _form_dict(ext.form_home_5),
            "form_away_5":  _form_dict(ext.form_away_5),
            "form_home_10": _form_dict(ext.form_home_10),
            "form_away_10": _form_dict(ext.form_away_10),
        }
```

- [ ] **Step 4: Lanciare i test (devono passare)**

Run: `uv run pytest tests/unit/test_feature_builder.py -v -m "not integration"`
Expected: tutti i test passano (4 nuovi + quelli esistenti).

- [ ] **Step 5: Verificare nessuna regressione**

Run: `uv run pytest -v -m "not integration"`
Expected: 28 + 4 = 32 unit test pass (numero esatto può variare se altri file ne aggiungono).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_feature_builder.py src/betfair_trading/services/feature_builder.py
git commit -m "feat(features): add _extract_teams, _build_a1, _build_a2 helpers"
```

---

## Task 3: Wiring completo con caches e persistenza external_feature_snapshots

**Files:**
- Create: `tests/integration/test_pipeline_a1_a2.py`
- Modify: `src/betfair_trading/services/feature_builder.py`

- [ ] **Step 1: Scrivere i 5 test di integrazione (failing)**

Create `tests/integration/test_pipeline_a1_a2.py`:

```python
"""End-to-end: FeatureBuilder produces A0+A1+A2 when ingestor is wired."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor
from betfair_trading.services.feature_builder import FeatureBuilder
from betfair_trading.services.market_collector import MarketCollector
from tests.integration.fakes.fake_betfair_client import FakeAsyncBetfairClient
from tests.integration.fakes.fixtures import make_book, make_market


@pytest.fixture
def team_mappings_file(tmp_path: Path) -> Path:
    p = tmp_path / "mappings.yaml"
    p.write_text('"Liverpool":\n  - "LFC"\n"Arsenal":\n  - "AFC"\n')
    return p


@pytest.fixture
def results_csv(tmp_path: Path) -> Path:
    p = tmp_path / "results.csv"
    rows = [
        ("01/03/2026", "Liverpool", "Arsenal", "H", 2, 0),  # ~asof-30d
        ("22/03/2026", "Liverpool", "Arsenal", "D", 1, 1),  # ~asof-10d
    ]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for r in rows:
            w.writerow(r)
    return p


async def _make_ingestor(pg_pool, team_mappings_file, results_csv=None):
    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher(team_mappings_file)
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)
    if results_csv is not None:
        await ingestor.load_historical_results(results_csv)
    return ingestor


async def test_a0_a1_a2_all_written_with_ingestor_loaded(
    pg_pool: asyncpg.Pool, team_mappings_file: Path, results_csv: Path
):
    ingestor = await _make_ingestor(pg_pool, team_mappings_file, results_csv)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", event_id="E-A",
                                home="Liverpool", away="Arsenal",
                                start_time=datetime.now(UTC) + timedelta(minutes=60)))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        ext_count = await conn.fetchval("SELECT COUNT(*) FROM external_feature_snapshots")
        rows = await conn.fetch(
            "SELECT runner_id, feature_set_version, ext_snapshot_id "
            "FROM feature_vectors WHERE market_id = '1.A' "
            "ORDER BY runner_id, feature_set_version"
        )

    assert ext_count == 1
    assert len(rows) == 9  # 3 runners * 3 versions
    versions_per_runner = {}
    ext_ids_a1_a2 = set()
    for r in rows:
        versions_per_runner.setdefault(r["runner_id"], set()).add(r["feature_set_version"])
        if r["feature_set_version"] in ("A1", "A2"):
            ext_ids_a1_a2.add(r["ext_snapshot_id"])
        if r["feature_set_version"] == "A0":
            assert r["ext_snapshot_id"] is None
    for runner_id, versions in versions_per_runner.items():
        assert versions == {"A0", "A1", "A2"}
    # All A1/A2 share the same ext_snapshot_id
    assert len(ext_ids_a1_a2) == 1


async def test_only_a0_when_ingestor_is_none(
    pg_pool: asyncpg.Pool
):
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        ext_count = await conn.fetchval("SELECT COUNT(*) FROM external_feature_snapshots")
        versions = await conn.fetch(
            "SELECT DISTINCT feature_set_version FROM feature_vectors WHERE market_id = '1.A'"
        )

    assert ext_count == 0
    assert {v["feature_set_version"] for v in versions} == {"A0"}


async def test_external_snapshot_cached_per_market(
    pg_pool: asyncpg.Pool, team_mappings_file: Path, results_csv: Path
):
    ingestor = await _make_ingestor(pg_pool, team_mappings_file, results_csv)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", home="Liverpool", away="Arsenal"))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        ext_count = await conn.fetchval("SELECT COUNT(*) FROM external_feature_snapshots")
        ext_ids = await conn.fetch(
            "SELECT DISTINCT ext_snapshot_id FROM feature_vectors "
            "WHERE market_id = '1.A' AND feature_set_version IN ('A1', 'A2')"
        )
        a1_a2_count = await conn.fetchval(
            "SELECT COUNT(*) FROM feature_vectors "
            "WHERE market_id = '1.A' AND feature_set_version IN ('A1', 'A2')"
        )

    assert ext_count == 1
    assert len(ext_ids) == 1  # all A1/A2 across 3 cycles share the same ext_snapshot_id
    assert a1_a2_count == 18  # 3 cycles * 3 runners * 2 versions (A1+A2)


async def test_a1_features_include_elo_a2_includes_form(
    pg_pool: asyncpg.Pool, team_mappings_file: Path, results_csv: Path
):
    ingestor = await _make_ingestor(pg_pool, team_mappings_file, results_csv)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A", home="Liverpool", away="Arsenal"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT feature_set_version, features FROM feature_vectors "
            "WHERE market_id = '1.A' AND runner_id = 101 "
            "ORDER BY feature_set_version"
        )

    payloads = {}
    for r in rows:
        f = r["features"]
        payloads[r["feature_set_version"]] = json.loads(f) if isinstance(f, str) else f

    a0, a1, a2 = payloads["A0"], payloads["A1"], payloads["A2"]

    # A0 must NOT contain elo/form keys
    assert "elo_home" not in a0
    assert "form_home_5" not in a0

    # A1 contains elo, NOT form
    assert "elo_home" in a1 and "elo_away" in a1 and "elo_delta" in a1
    assert "match_confidence" in a1
    assert "form_home_5" not in a1
    # Elo values are non-default after history load (Liverpool home wins 2-0 first)
    assert a1["elo_home"] != 1500.0

    # A2 contains both elo and form (form_home_5 should have at least 1 match data)
    assert "elo_home" in a2 and "form_home_5" in a2
    assert a2["form_home_5"] is not None  # 2 matches available pre-asof
    assert "points_per_match" in a2["form_home_5"]


async def test_low_confidence_team_match_persists_a1_a2(
    pg_pool: asyncpg.Pool, tmp_path: Path
):
    """Team unresolved (LOW confidence): A1/A2 still written, ext_snapshot has match_confidence=LOW."""
    # Mappings only Liverpool, away team unknown
    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n')

    ingestor = await _make_ingestor(pg_pool, mapping_yaml, results_csv=None)

    fake = FakeAsyncBetfairClient()
    # away="ZZZ Unknown" must match runner_name on sort_priority=3
    fake.add_market(make_market(market_id="1.A", home="Liverpool", away="ZZZ Unknown"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=ingestor)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        ext_row = await conn.fetchrow(
            "SELECT ext_snapshot_id, match_confidence, quality_flags "
            "FROM external_feature_snapshots LIMIT 1"
        )
        a1_a2_count = await conn.fetchval(
            "SELECT COUNT(*) FROM feature_vectors "
            "WHERE market_id = '1.A' AND feature_set_version IN ('A1', 'A2')"
        )
        ext_ids = await conn.fetch(
            "SELECT DISTINCT ext_snapshot_id FROM feature_vectors "
            "WHERE market_id = '1.A' AND feature_set_version IN ('A1', 'A2')"
        )

    assert ext_row is not None
    assert ext_row["match_confidence"] == "LOW"
    flags = ext_row["quality_flags"]
    if isinstance(flags, str):
        flags = json.loads(flags)
    assert flags["away_confidence"] == 0.0
    assert flags["home_confidence"] == 1.0
    assert a1_a2_count == 6  # 3 runners * 2 versions
    assert len(ext_ids) == 1
    assert ext_ids[0]["ext_snapshot_id"] == ext_row["ext_snapshot_id"]
```

- [ ] **Step 2: Lanciare i test (devono fallire)**

Run: `uv run pytest tests/integration/test_pipeline_a1_a2.py -v -m integration`
Expected: 5 FAILED. Errori probabili:
- `ext_count == 0` perché il FeatureBuilder ancora non scrive `external_feature_snapshots`
- `versions == {"A0"}` perché ancora non scrive A1/A2

Conferma che i test "sentono" l'assenza del wiring. Procedere all'implementazione.

- [ ] **Step 3: Implementare il wiring nel FeatureBuilder**

In `src/betfair_trading/services/feature_builder.py`:

Aggiungere/aggiornare imports in cima al file:

```python
"""Feature Builder: merges market snapshots with external features
into versioned feature vectors."""

import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from betfair_trading.db.writer import (
    insert_external_feature_snapshot,
    insert_feature_vector,
)
from betfair_trading.models.external import ExternalFeatureBundle
from betfair_trading.models.features import FeatureSetVersion, FeatureVector
from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.external_ingestor import ExternalDataIngestor

log = structlog.get_logger()
```

Sostituire la classe `FeatureBuilder` esistente con (preservando i 3 helper statici aggiunti in Task 2):

```python
class FeatureBuilder:
    def __init__(
        self, db_pool: asyncpg.Pool, external_ingestor: ExternalDataIngestor | None = None
    ):
        self._pool = db_pool
        self._ingestor = external_ingestor
        # Caches per market_id (live finché il processo è up)
        self._ext_id_cache: dict[str, uuid.UUID] = {}
        self._ext_bundle_cache: dict[str, ExternalFeatureBundle] = {}
        self._runner_meta_cache: dict[str, list[Runner]] = {}

    async def on_market_snapshot(
        self, bundle: MarketSnapshotBundle, snapshot_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Called by MarketCollector after each poll cycle.
        Builds and persists A0 always; A1+A2 if external_ingestor is wired.
        """
        ext_snapshot_id, ext_bundle = await self._get_or_create_external(bundle)
        feature_vector_ids: list[uuid.UUID] = []

        async with self._pool.acquire() as conn:
            for i, runner in enumerate(bundle.runners):
                snap_id = snapshot_ids[i] if i < len(snapshot_ids) else None
                a0 = self._build_a0(bundle, runner)
                feature_vector_ids.append(
                    await insert_feature_vector(conn, FeatureVector(
                        market_id=bundle.market_id, event_id=bundle.event_id,
                        runner_id=runner.runner_id,
                        feature_set_version=FeatureSetVersion.A0,
                        snapshot_id=snap_id, ext_snapshot_id=None,
                        features=a0, generated_at=datetime.now(UTC),
                    ))
                )
                if ext_snapshot_id is not None and ext_bundle is not None:
                    a1 = self._build_a1(a0, ext_bundle)
                    a2 = self._build_a2(a1, ext_bundle)
                    feature_vector_ids.append(
                        await insert_feature_vector(conn, FeatureVector(
                            market_id=bundle.market_id, event_id=bundle.event_id,
                            runner_id=runner.runner_id,
                            feature_set_version=FeatureSetVersion.A1,
                            snapshot_id=snap_id, ext_snapshot_id=ext_snapshot_id,
                            features=a1, generated_at=datetime.now(UTC),
                        ))
                    )
                    feature_vector_ids.append(
                        await insert_feature_vector(conn, FeatureVector(
                            market_id=bundle.market_id, event_id=bundle.event_id,
                            runner_id=runner.runner_id,
                            feature_set_version=FeatureSetVersion.A2,
                            snapshot_id=snap_id, ext_snapshot_id=ext_snapshot_id,
                            features=a2, generated_at=datetime.now(UTC),
                        ))
                    )

        log.debug(
            "features_built",
            market_id=bundle.market_id,
            with_external=ext_snapshot_id is not None,
            vectors=len(feature_vector_ids),
        )
        return feature_vector_ids

    async def _get_or_create_external(
        self, bundle: MarketSnapshotBundle
    ) -> tuple[uuid.UUID | None, ExternalFeatureBundle | None]:
        """Idempotent: returns cached ext_snapshot_id for the market if seen, else
        computes the ExternalFeatureBundle and persists it once."""
        if self._ingestor is None:
            return None, None
        if bundle.market_id in self._ext_id_cache:
            return (
                self._ext_id_cache[bundle.market_id],
                self._ext_bundle_cache[bundle.market_id],
            )

        async with self._pool.acquire() as conn:
            runners = await self._load_runner_metadata(conn, bundle.market_id)
            if not runners:
                # Market not yet in `runners` table — should not happen post-discovery,
                # but defensive: skip A1/A2 this cycle, retry next.
                log.warning("ext_skip_no_runner_meta", market_id=bundle.market_id)
                return None, None
            home, away = self._extract_teams(runners)
            ext_bundle = self._ingestor.get_features_asof(
                home_team=home, away_team=away,
                asof_ts=bundle.snapshot_ts, market_id=bundle.market_id,
            )
            ext_id = await insert_external_feature_snapshot(conn, ext_bundle)

        self._ext_id_cache[bundle.market_id] = ext_id
        self._ext_bundle_cache[bundle.market_id] = ext_bundle
        return ext_id, ext_bundle

    async def _load_runner_metadata(
        self, conn: asyncpg.Connection, market_id: str
    ) -> list[Runner]:
        """Cached per market_id. Loads runner_name+sort_priority from `runners` table."""
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
        self._runner_meta_cache[market_id] = runners
        return runners

    @staticmethod
    def _build_a0(bundle: MarketSnapshotBundle, runner) -> dict:
        """A0: Market-only features."""
        back_price = float(runner.best_back_price) if runner.best_back_price else None
        lay_price = float(runner.best_lay_price) if runner.best_lay_price else None

        implied_prob_raw = None
        if back_price and back_price > 0:
            implied_prob_raw = 1.0 / back_price

        mid_price = None
        if back_price and lay_price:
            mid_price = (back_price + lay_price) / 2.0

        return {
            "best_back": back_price,
            "best_lay": lay_price,
            "best_back_size": float(runner.best_back_size) if runner.best_back_size else None,
            "best_lay_size": float(runner.best_lay_size) if runner.best_lay_size else None,
            "spread": float(runner.spread) if runner.spread else None,
            "mid_price": mid_price,
            "traded_volume": float(runner.traded_volume),
            "total_matched": float(bundle.total_matched) if bundle.total_matched else None,
            "implied_prob_raw": implied_prob_raw,
            "minutes_to_start": bundle.minutes_to_start,
            "market_status": bundle.market_status,
            "inplay": bundle.inplay,
        }

    @staticmethod
    def _extract_teams(runners: list[Runner]) -> tuple[str, str]:
        """Betfair Match Odds: sort_priority 1=home, 2=draw, 3=away."""
        sorted_runners = sorted(
            runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
        )
        return sorted_runners[0].runner_name, sorted_runners[-1].runner_name

    @staticmethod
    def _build_a1(a0: dict, ext: ExternalFeatureBundle) -> dict:
        """A1 = A0 + Elo fields. Same fields for all runners; runner_id distinguishes."""
        return {
            **a0,
            "elo_home": float(ext.elo_home) if ext.elo_home is not None else None,
            "elo_away": float(ext.elo_away) if ext.elo_away is not None else None,
            "elo_delta": float(ext.elo_delta) if ext.elo_delta is not None else None,
            "match_confidence": ext.match_confidence,
        }

    @staticmethod
    def _build_a2(a1: dict, ext: ExternalFeatureBundle) -> dict:
        """A2 = A1 + form fields (home/away, n=5/10)."""
        def _form_dict(f):
            if f is None:
                return None
            return {
                "points_per_match": f.points_per_match,
                "goal_diff_per_match": f.goal_diff_per_match,
                "win_rate": f.win_rate,
            }
        return {
            **a1,
            "form_home_5":  _form_dict(ext.form_home_5),
            "form_away_5":  _form_dict(ext.form_away_5),
            "form_home_10": _form_dict(ext.form_home_10),
            "form_away_10": _form_dict(ext.form_away_10),
        }
```

- [ ] **Step 4: Lanciare i test integrazione (devono passare)**

Run: `uv run pytest tests/integration/test_pipeline_a1_a2.py -v -m integration`
Expected: 5 PASSED.

Se uno fallisce:
- Su `test_a1_features_include_elo_a2_includes_form`: l'asserzione `a1["elo_home"] != 1500.0` richiede che la history sia stata caricata (file CSV ha 2 partite). Se elo_home è 1500.0 esatto, controllare che `_make_ingestor` stia chiamando `load_historical_results` e che `is_loaded` sia `True`.
- Su `test_low_confidence_team_match_persists_a1_a2`: la `home_confidence` deve essere `1.0` (Liverpool è nel YAML con alias `LFC`). La `away_confidence` deve essere `0.0`. Se entrambe sono 0.0, il YAML non è stato letto: verificare il path passato a `TeamMatcher`.

NON modificare il codice di produzione per far passare i test se i test sono sbagliati. Se sospetti un bug nel test, riportalo e ferma.

- [ ] **Step 5: Verificare nessuna regressione su unit + altri integration**

Run: `uv run pytest -v -m "not integration"`
Expected: tutti gli unit test (32+) PASS.

Run: `uv run pytest -v -m integration`
Expected: tutti gli integration test (20 esistenti + 5 nuovi = 25) PASS.

I test esistenti in `test_pipeline_feature_builder.py` continuano a passare perché chiamano `FeatureBuilder(pool, external_ingestor=None)` o omettono il param (default `None`) → solo A0, comportamento invariato.

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/ tests/`
Expected: All checks passed!

Se ci sono errori, correggerli e ri-eseguire.

Run: `uv run ruff format src/ tests/`
Expected: nessuna modifica (idempotente). Se ne applica, è OK.

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_pipeline_a1_a2.py src/betfair_trading/services/feature_builder.py
git commit -m "feat(features): wire A1/A2 with external_feature_snapshots persistence and per-market cache"
```

Se ruff ha applicato format, fare un commit separato:

```bash
git add -A
git commit -m "chore: ruff format"
```

---

## Task 4: Verifica finale + push branch

**Files:** nessun file modificato — solo verifica e push.

- [ ] **Step 1: Suite completa**

Run: `uv run pytest -v`
Expected: tutti i test PASS (52 total: 32 unit + 25 integration con margine per nuovi).

Misurare il tempo: la suite integration deve restare sotto i 10s. Se molto più lenta del solito (>30s), investigare (probabile problema di pool/connessioni).

- [ ] **Step 2: Lint**

Run: `uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 3: Push del feature branch**

```bash
git push -u origin feature/a1-a2-feature-wiring
```

Il push restituisce un URL per aprire la PR su GitHub.

- [ ] **Step 4: Aprire la PR**

Aprire l'URL restituito dal push. Title e body suggeriti:

**Title:** `feat: wire A1/A2 features (market+Elo, market+Elo+form)`

**Body:**

```markdown
## Summary
- Cabla `ExternalDataIngestor` dentro `FeatureBuilder` per produrre feature_vector A1 (market+Elo) e A2 (market+Elo+form) accanto al baseline A0
- Persistente automatica in `external_feature_snapshots` con cache per-market (1 INSERT per market)
- Aggiunto `history_loaded` ai `quality_flags` per visibilità della completezza dei dati esterni

## Behavior
- Se `external_ingestor=None`: solo A0 (compatibilità retroattiva con test esistenti)
- Se ingestor disponibile: scrive A0+A1+A2 per ogni runner di ogni snapshot
- ext_snapshot calcolato 1 volta per market al primo snapshot (cached); A1/A2 successivi riusano l'ID

## Tests
- 4 nuovi unit test (`_extract_teams`, `_build_a1`, `_build_a2`, history_loaded flag)
- 5 nuovi integration test (full ablation, ingestor=None, cache, payload shape, low confidence)

## Reference
- Spec: `docs/superpowers/specs/2026-05-08-a1-a2-feature-wiring-design.md`
- Plan: `docs/superpowers/plans/2026-05-08-a1-a2-feature-wiring.md`

## Test plan
- [ ] `uv run pytest -v` → all PASS
- [ ] `uv run ruff check src/ tests/` → clean
```

---

## Note finali

- **Eviction cache**: i tre dict in `FeatureBuilder` non hanno eviction. Cardinalità tipica ~100 markets attivi → memoria O(KB). Follow-up se la profondità di tracking esplode.
- **`feature_hash` deterministico**: A1 e A2 ereditano la determinatezza da A0 (stessi input → stesso JSON canonico → stesso SHA256). I test esistenti in `test_pipeline_feature_builder.py::test_feature_hash_deterministic` continuano a passare per A0; i test A1/A2 in questo plan non lo verificano esplicitamente (non è la novità). Aggiungibile in un follow-up se serve esplicito.
- **Bug nel codice di produzione scoperti durante TDD**: NON fixare in questo plan. Aprire follow-up plan separato.

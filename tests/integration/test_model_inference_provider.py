"""Integration tests for ModelInferenceProvider."""

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


async def _seed_model_version(
    pg_pool, file_path: Path, model_name: str = "test_v1"
) -> ModelVersion:
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


async def test_initialize_no_model_falls_back(pg_pool: asyncpg.Pool, tmp_path: Path):
    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

    assert provider.model_version == "STUB_NO_MODEL"

    bundle, runners = _make_bundle_and_runners_for_test()
    probs, inference_id = await provider.get_probabilities(bundle, runners, feature_vector_ids=[])

    assert inference_id is None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


async def test_get_probabilities_persists_inference(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    mv = await _seed_model_version(pg_pool, trained_model_on_disk)

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
    fake.add_market(
        make_market(
            market_id="1.A",
            event_id="E-A",
            home="Liverpool",
            away="Arsenal",
            start_time=datetime.now(UTC) + timedelta(minutes=60),
        )
    )
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

    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()

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
    total = float(row["p_home"]) + float(row["p_draw"]) + float(row["p_away"])
    assert total == pytest.approx(1.0, abs=1e-6)


async def test_get_probabilities_falls_back_when_no_a2(
    pg_pool: asyncpg.Pool, trained_model_on_disk: Path, tmp_path: Path
):
    """Model loaded but no A2 feature_vector available → market-implied fallback."""
    await _seed_model_version(pg_pool, trained_model_on_disk)

    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
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
    fake.add_market(make_market(market_id="1.A", event_id="E-A", home="Liverpool", away="Arsenal"))
    fake.queue_book("1.A", make_book(market_id="1.A", event_id="E-A"))

    provider = ModelInferenceProvider(pool=pg_pool, models_dir=tmp_path)
    await provider.initialize()
    engine = DecisionEngine(
        pool=pg_pool,
        provider=provider,
        edge_threshold=-1.0,
        min_liquidity=0.0,
        max_spread=999.0,
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
# Local helper
# ---------------------------------------------------------------------------


def _make_bundle_and_runners_for_test():
    from decimal import Decimal

    from betfair_trading.models.market import (
        MarketSnapshotBundle,
        Runner,
        RunnerSnapshot,
    )

    bundle = MarketSnapshotBundle(
        market_id="1.A",
        event_id="E-A",
        snapshot_ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        runners=[
            RunnerSnapshot(
                runner_id=101,
                best_back_price=Decimal("2.0"),
                best_lay_price=Decimal("2.04"),
                traded_volume=Decimal("0"),
            ),
            RunnerSnapshot(
                runner_id=102,
                best_back_price=Decimal("3.5"),
                best_lay_price=Decimal("3.6"),
                traded_volume=Decimal("0"),
            ),
            RunnerSnapshot(
                runner_id=103,
                best_back_price=Decimal("4.0"),
                best_lay_price=Decimal("4.1"),
                traded_volume=Decimal("0"),
            ),
        ],
        market_status="OPEN",
        inplay=False,
        total_matched=Decimal("1000"),
        minutes_to_start=60.0,
    )
    runners = [
        Runner(runner_id=101, runner_name="Liverpool", sort_priority=1),
        Runner(runner_id=102, runner_name="The Draw", sort_priority=2),
        Runner(runner_id=103, runner_name="Arsenal", sort_priority=3),
    ]
    return bundle, runners

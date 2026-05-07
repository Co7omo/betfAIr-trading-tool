"""End-to-end: snapshot → FeatureBuilder.on_market_snapshot → feature_vectors persisted."""

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

    assert len(rows) == 3  # 3 runners
    versions = {r["feature_set_version"] for r in rows}
    assert versions == {"A0"}
    assert all(r["snapshot_id"] is not None for r in rows)
    assert all(r["ext_snapshot_id"] is None for r in rows)


async def test_feature_hash_deterministic(pg_pool: asyncpg.Pool):
    """Same bundle as input → same feature_hash (SHA256 of canonical JSON)."""
    fake = FakeAsyncBetfairClient()
    start_time = datetime.now(UTC) + timedelta(minutes=60)
    fake.add_market(make_market(market_id="1.A", start_time=start_time))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))  # IDENTICAL to the first

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        # feature_hash is a Pydantic computed_field, NOT a persisted column on feature_vectors
        # — recompute equivalence by comparing the `features` payloads
        rows = await conn.fetch(
            "SELECT runner_id, features FROM feature_vectors "
            "WHERE market_id = '1.A' AND runner_id = 101 ORDER BY generated_at"
        )

    assert len(rows) == 2
    # The features should be structurally identical between the two cycles
    import json
    f1 = json.loads(rows[0]["features"]) if isinstance(rows[0]["features"], str) else rows[0]["features"]
    f2 = json.loads(rows[1]["features"]) if isinstance(rows[1]["features"], str) else rows[1]["features"]
    # `minutes_to_start` is hardcoded in make_book → identical
    assert f1 == f2


async def test_feature_vector_links_correct_snapshot(pg_pool: asyncpg.Pool):
    """Each feature_vector must point to its own snapshot_id."""
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

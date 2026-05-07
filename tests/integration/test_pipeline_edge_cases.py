"""End-to-end edge cases: outside window, suspended market, entity match miss."""

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
    """Market with start_time outside T-120/T-10 → discovered but not polled."""
    fake = FakeAsyncBetfairClient()
    now = datetime.now(UTC)

    # In window (T-60min)
    fake.add_market(make_market(market_id="1.IN", start_time=now + timedelta(minutes=60)))
    fake.queue_book("1.IN", make_book(market_id="1.IN"))
    # Beyond window_start (T-200min: too far in the future)
    fake.add_market(make_market(market_id="1.FAR", start_time=now + timedelta(minutes=200)))
    fake.queue_book("1.FAR", make_book(market_id="1.FAR"))
    # Below window_end (T-5min: too close to kick-off)
    fake.add_market(make_market(market_id="1.NEAR", start_time=now + timedelta(minutes=5)))
    fake.queue_book("1.NEAR", make_book(market_id="1.NEAR"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    await collector.run_discovery()
    snapshots = await collector.run_poll_cycle()

    # Only "1.IN" is eligible → 3 runner snapshots
    assert snapshots == 3

    async with pg_pool.acquire() as conn:
        market_ids_with_snapshots = await conn.fetch(
            "SELECT DISTINCT market_id FROM market_snapshots"
        )
    assert {r["market_id"] for r in market_ids_with_snapshots} == {"1.IN"}


async def test_suspended_market_snapshot_recorded(pg_pool: asyncpg.Pool):
    """Market SUSPENDED: snapshot still recorded (audit-first), market_status='SUSPENDED'."""
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
    """Even on SUSPENDED markets, the FeatureBuilder still produces A0 (audit completeness)."""
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A", status="SUSPENDED"))

    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM feature_vectors WHERE market_id = '1.A'")
    assert count == 3


async def test_entity_match_miss_does_not_break_pipeline(pg_pool: asyncpg.Pool, tmp_path: Path):
    """Team unknown to TeamMatcher → match_confidence='LOW' but external snapshot still written.
    The market pipeline (snapshot + A0 feature_vector) is independent and continues.
    """
    # YAML mapping has only one known team; the other is missing
    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n')

    elo = EloEngine()
    form = FormCalculator()
    matcher = TeamMatcher(mapping_yaml)

    asof = datetime.now(UTC)
    bundle_ext = ExternalDataIngestor(elo, form, matcher, pg_pool).get_features_asof(
        home_team="Liverpool",
        away_team="ZZZ Unknown FC",  # not in mappings → confidence=0.0 → match_confidence=LOW
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

    # The market pipeline continues normally
    fake = FakeAsyncBetfairClient()
    fake.add_market(make_market(market_id="1.A"))
    fake.queue_book("1.A", make_book(market_id="1.A"))
    collector = MarketCollector(fake, pg_pool, window_start_minutes=120, window_end_minutes=10)
    fb = FeatureBuilder(pg_pool, external_ingestor=None)
    await collector.run_discovery()
    await collector.run_poll_cycle(on_snapshot=fb.on_market_snapshot)

    async with pg_pool.acquire() as conn:
        snap_count = await conn.fetchval(
            "SELECT COUNT(*) FROM market_snapshots WHERE market_id = $1", "1.A"
        )
        fv_count = await conn.fetchval(
            "SELECT COUNT(*) FROM feature_vectors WHERE market_id = $1", "1.A"
        )
    assert snap_count == 3
    assert fv_count == 3

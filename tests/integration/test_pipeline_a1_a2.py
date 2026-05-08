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
        ("01/03/2026", "Liverpool", "Arsenal", "H", 2, 0),
        ("22/03/2026", "Liverpool", "Arsenal", "D", 1, 1),
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
    for _runner_id, versions in versions_per_runner.items():
        assert versions == {"A0", "A1", "A2"}
    assert len(ext_ids_a1_a2) == 1


async def test_only_a0_when_ingestor_is_none(pg_pool: asyncpg.Pool):
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
    assert len(ext_ids) == 1
    assert a1_a2_count == 18  # 3 cycles * 3 runners * 2 versions


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

    assert "elo_home" not in a0
    assert "form_home_5" not in a0

    assert "elo_home" in a1 and "elo_away" in a1 and "elo_delta" in a1
    assert "match_confidence" in a1
    assert "form_home_5" not in a1
    assert a1["elo_home"] != 1500.0

    assert "elo_home" in a2 and "form_home_5" in a2
    assert a2["form_home_5"] is not None
    assert "points_per_match" in a2["form_home_5"]


async def test_low_confidence_team_match_persists_a1_a2(pg_pool: asyncpg.Pool, tmp_path: Path):
    """Team unresolved (LOW): A1/A2 still written, ext_snapshot.match_confidence='LOW'."""
    mapping_yaml = tmp_path / "mappings.yaml"
    mapping_yaml.write_text('"Liverpool":\n  - "LFC"\n')

    ingestor = await _make_ingestor(pg_pool, mapping_yaml, results_csv=None)

    fake = FakeAsyncBetfairClient()
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

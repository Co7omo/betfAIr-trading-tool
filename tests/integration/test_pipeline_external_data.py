"""End-to-end: ExternalDataIngestor → Elo/form as-of → external_feature_snapshots."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from betfair_trading.db.writer import insert_external_feature_snapshot
from betfair_trading.elo.engine import EloEngine
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.services.external_ingestor import ExternalDataIngestor


@pytest.fixture
def team_mappings_file(tmp_path: Path) -> Path:
    """Minimal mappings file that registers Liverpool and Arsenal as canonical names.

    Liverpool and Arsenal are not in config/team_mappings.yaml, so TeamMatcher
    with an empty (or default) mappings file would return confidence=0.0 for them,
    causing match_confidence="LOW". We provide an explicit per-test mappings file
    so that resolve() finds them in self._mappings and returns confidence=1.0.
    """
    p = tmp_path / "mappings.yaml"
    p.write_text('"Liverpool":\n  - "Liverpool FC"\n"Arsenal":\n  - "Arsenal FC"\n')
    return p


@pytest.fixture
def results_csv(tmp_path: Path) -> Path:
    """4 Liverpool vs Arsenal matches: -30d, -10d, +5d, +10d relative to 2026-04-01."""
    p = tmp_path / "results.csv"
    rows = [
        ("01/03/2026", "Liverpool", "Arsenal", "H", 2, 0),  # d-30
        ("22/03/2026", "Liverpool", "Arsenal", "D", 1, 1),  # d-10
        ("06/04/2026", "Liverpool", "Arsenal", "A", 0, 1),  # d+5  (future for asof=2026-04-01)
        ("11/04/2026", "Arsenal", "Liverpool", "H", 3, 0),  # d+10 (future)
    ]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG"])
        for r in rows:
            w.writerow(r)
    return p


async def test_load_historical_results_populates_elo_form(
    pg_pool: asyncpg.Pool, results_csv: Path, team_mappings_file: Path
):
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher(team_mappings_file)
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)

    n = await ingestor.load_historical_results(results_csv)

    assert n == 4
    # After 4 matches, Elo is no longer at defaults
    assert elo.history_size == 4
    assert elo.get_rating("Liverpool") != 1500.0
    assert elo.get_rating("Arsenal") != 1500.0


async def test_asof_excludes_future_matches(
    pg_pool: asyncpg.Pool, results_csv: Path, team_mappings_file: Path
):
    """Anti-leakage: asof_ts=2026-04-01 must NOT reflect d+5 and d+10 matches."""
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher(team_mappings_file)
    ingestor = ExternalDataIngestor(elo, form, matcher, pg_pool)

    await ingestor.load_historical_results(results_csv)

    asof = datetime(2026, 4, 1, tzinfo=UTC)

    # Manual expected calculation: after the first 2 matches
    # Match 1 (d-30): Liverpool beats Arsenal at home
    #   exp_home = 1/(1+10^0) = 0.5; new_home = 1500 + 20*(1-0.5) = 1510
    #   new_away = 1500 + 20*(0-0.5) = 1490
    # Match 2 (d-10): Liverpool 1-1 Arsenal at home
    #   delta = 1490-1510 = -20; exp_home = 1/(1+10^(-20/400)) ≈ 0.5288
    #   new_home = 1510 + 20*(0.5-0.5288) = 1509.42 (~)
    #   new_away = 1490 + 20*(0.5-0.4712) = 1490.57 (~)

    elo_home, elo_away = elo.get_ratings_asof("Liverpool", "Arsenal", asof)
    assert 1508.0 < elo_home < 1511.0, f"unexpected elo_home={elo_home}"
    assert 1489.0 < elo_away < 1492.0, f"unexpected elo_away={elo_away}"

    # Liverpool's form for last 5 matches available before asof = 2 matches (W, D)
    f5_lpool = form.compute_form("Liverpool", asof, n=5)
    assert f5_lpool is not None
    assert f5_lpool.points_per_match == pytest.approx((3 + 1) / 2)  # W=3, D=1
    assert f5_lpool.win_rate == pytest.approx(0.5)


async def test_external_snapshot_persisted(
    pg_pool: asyncpg.Pool, results_csv: Path, team_mappings_file: Path
):
    """ExternalDataIngestor.get_features_asof() + insert_external_feature_snapshot
    produce a row in external_feature_snapshots.
    """
    elo = EloEngine(k_factor=20.0, initial_rating=1500.0)
    form = FormCalculator()
    matcher = TeamMatcher(team_mappings_file)
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
    # elo_delta is rounded independently from the raw float, not derived from the
    # already-rounded elo_home/elo_away, so it may differ by up to ±0.01 due to
    # Decimal(str(round(..., 2))) applied separately to each field.
    assert abs(float(row["elo_delta"]) - (float(row["elo_home"]) - float(row["elo_away"]))) <= 0.01
    assert row["form_home_5"] is not None  # 2 matches available pre-asof
    assert row["match_confidence"] == "HIGH"  # confidence=1.0 (canonical names in mappings file)
    flags = row["quality_flags"]
    if isinstance(flags, str):
        flags = json.loads(flags)
    assert flags.get("home_confidence") == 1.0
    assert flags.get("away_confidence") == 1.0

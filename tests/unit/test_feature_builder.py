"""Tests for A0 feature builder logic."""

from datetime import UTC, datetime
from decimal import Decimal

from betfair_trading.models.external import ExternalFeatureBundle, FormFeatures
from betfair_trading.models.market import MarketSnapshotBundle, Runner, RunnerSnapshot
from betfair_trading.services.feature_builder import FeatureBuilder


def test_build_a0_with_full_data():
    bundle = MarketSnapshotBundle(
        market_id="1.234",
        event_id="99",
        snapshot_ts=datetime(2025, 6, 1, 14, 0, tzinfo=UTC),
        runners=[],
        market_status="OPEN",
        inplay=False,
        total_matched=Decimal("50000"),
        minutes_to_start=60.0,
    )

    runner = RunnerSnapshot(
        runner_id=1,
        best_back_price=Decimal("2.50"),
        best_back_size=Decimal("200.00"),
        best_lay_price=Decimal("2.52"),
        best_lay_size=Decimal("150.00"),
        spread=Decimal("0.02"),
        traded_volume=Decimal("10000.00"),
    )

    features = FeatureBuilder._build_a0(bundle, runner)

    assert features["best_back"] == 2.5
    assert features["best_lay"] == 2.52
    assert features["spread"] == 0.02
    assert features["traded_volume"] == 10000.0
    assert features["total_matched"] == 50000.0
    assert abs(features["implied_prob_raw"] - 0.4) < 0.001
    assert features["mid_price"] == 2.51
    assert features["minutes_to_start"] == 60.0
    assert features["inplay"] is False


def test_build_a0_with_missing_prices():
    bundle = MarketSnapshotBundle(
        market_id="1.234",
        event_id="99",
        snapshot_ts=datetime(2025, 6, 1, 14, 0, tzinfo=UTC),
        runners=[],
        market_status="SUSPENDED",
        inplay=False,
        minutes_to_start=5.0,
    )

    runner = RunnerSnapshot(
        runner_id=1,
        traded_volume=Decimal("0"),
    )

    features = FeatureBuilder._build_a0(bundle, runner)

    assert features["best_back"] is None
    assert features["best_lay"] is None
    assert features["spread"] is None
    assert features["mid_price"] is None
    assert features["implied_prob_raw"] is None
    assert features["traded_volume"] == 0.0


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
    """Fallback: None sort_priority sorts last, doesn't crash."""
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
        home_team="Liverpool",
        away_team="Arsenal",
        elo_home=Decimal("1510.50"),
        elo_away=Decimal("1490.50"),
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
        "best_back": 2.0,
        "elo_home": 1510.0,
        "elo_away": 1490.0,
        "elo_delta": 20.0,
        "match_confidence": "HIGH",
    }
    form_h5 = FormFeatures(
        points_per_match=2.0,
        goal_diff_per_match=1.5,
        win_rate=0.6,
        draw_rate=0.2,
        loss_rate=0.2,
    )
    ext = ExternalFeatureBundle(
        event_key="L_vs_A",
        asof_ts=datetime(2026, 4, 1, tzinfo=UTC),
        home_team="Liverpool",
        away_team="Arsenal",
        form_home_5=form_h5,
        form_away_5=None,
        form_home_10=None,
        form_away_10=None,
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

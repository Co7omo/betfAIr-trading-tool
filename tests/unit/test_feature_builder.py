"""Tests for A0 feature builder logic."""

from datetime import UTC, datetime
from decimal import Decimal

from betfair_trading.models.market import MarketSnapshotBundle, RunnerSnapshot
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

"""Tests for Pydantic model validation and feature hash stability."""

from datetime import UTC, datetime
from decimal import Decimal

from betfair_trading.models.features import FeatureSetVersion, FeatureVector
from betfair_trading.models.market import MarketSnapshotBundle, RunnerSnapshot


def test_runner_snapshot_creation():
    runner = RunnerSnapshot(
        runner_id=12345,
        best_back_price=Decimal("2.50"),
        best_back_size=Decimal("100.00"),
        best_lay_price=Decimal("2.52"),
        best_lay_size=Decimal("50.00"),
        spread=Decimal("0.02"),
        traded_volume=Decimal("5000.00"),
    )
    assert runner.runner_id == 12345
    assert runner.spread == Decimal("0.02")


def test_market_snapshot_bundle_creation():
    bundle = MarketSnapshotBundle(
        market_id="1.234567",
        event_id="99999",
        snapshot_ts=datetime(2025, 6, 1, 14, 0, tzinfo=UTC),
        runners=[
            RunnerSnapshot(runner_id=1, traded_volume=Decimal("1000")),
            RunnerSnapshot(runner_id=2, traded_volume=Decimal("2000")),
            RunnerSnapshot(runner_id=3, traded_volume=Decimal("500")),
        ],
        market_status="OPEN",
        inplay=False,
        minutes_to_start=60.0,
    )
    assert len(bundle.runners) == 3
    assert bundle.market_status == "OPEN"


def test_feature_vector_hash_determinism():
    """Same features must always produce the same hash."""
    features = {"best_back": 2.5, "spread": 0.02, "volume": 1000}
    ts = datetime(2025, 6, 1, tzinfo=UTC)

    fv1 = FeatureVector(
        market_id="1.234",
        event_id="99",
        runner_id=1,
        feature_set_version=FeatureSetVersion.A0,
        features=features,
        generated_at=ts,
    )
    fv2 = FeatureVector(
        market_id="1.234",
        event_id="99",
        runner_id=1,
        feature_set_version=FeatureSetVersion.A0,
        features=features,
        generated_at=ts,
    )

    assert fv1.feature_hash == fv2.feature_hash


def test_feature_vector_hash_changes_with_data():
    ts = datetime(2025, 6, 1, tzinfo=UTC)
    fv1 = FeatureVector(
        market_id="1.234",
        event_id="99",
        runner_id=1,
        feature_set_version=FeatureSetVersion.A0,
        features={"best_back": 2.5},
        generated_at=ts,
    )
    fv2 = FeatureVector(
        market_id="1.234",
        event_id="99",
        runner_id=1,
        feature_set_version=FeatureSetVersion.A0,
        features={"best_back": 3.0},
        generated_at=ts,
    )

    assert fv1.feature_hash != fv2.feature_hash


def test_feature_set_versions():
    assert FeatureSetVersion.A0 == "A0"
    assert FeatureSetVersion.A1 == "A1"
    assert FeatureSetVersion.A2 == "A2"

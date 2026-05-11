"""Unit tests for ProbabilityProvider stub implementations."""

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from betfair_trading.models.market import (
    MarketSnapshotBundle,
    Runner,
    RunnerSnapshot,
)
from betfair_trading.services.probability_providers import (
    BiasedStubProvider,
    MarketImpliedProvider,
)


def _make_bundle_and_runners():
    bundle = MarketSnapshotBundle(
        market_id="1.A",
        event_id="E-A",
        snapshot_ts=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
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


@pytest.mark.asyncio
async def test_market_implied_provider_returns_normalized_probs():
    bundle, runners = _make_bundle_and_runners()
    provider = MarketImpliedProvider()
    probs = await provider.get_probabilities(bundle, runners, feature_vector_ids=[])

    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    # Home (odds 2.0) should have largest implied prob
    assert probs[101] > probs[102]
    assert probs[101] > probs[103]


@pytest.mark.asyncio
async def test_market_implied_provider_version():
    provider = MarketImpliedProvider()
    assert provider.model_version == "STUB_MARKET_IMPLIED_V1"


@pytest.mark.asyncio
async def test_biased_stub_provider_shifts_home():
    bundle, runners = _make_bundle_and_runners()
    market_provider = MarketImpliedProvider()
    market_probs = await market_provider.get_probabilities(bundle, runners, [])

    biased_provider = BiasedStubProvider(home_bias=0.05)
    biased_probs = await biased_provider.get_probabilities(bundle, runners, [])

    # Home should get more weight after bias
    assert biased_probs[101] > market_probs[101]
    # Sum still equals 1.0
    total = sum(biased_probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_biased_stub_provider_version():
    provider = BiasedStubProvider(home_bias=0.05)
    assert provider.model_version == "STUB_BIAS_V1"

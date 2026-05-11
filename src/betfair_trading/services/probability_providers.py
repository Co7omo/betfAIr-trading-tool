"""ProbabilityProvider Protocol + stub implementations for Phase 2.

The real Model Inference will provide a third implementation in Phase 3.
"""

import uuid
from typing import Protocol

from betfair_trading.models.market import MarketSnapshotBundle, Runner
from betfair_trading.services.edge import compute_market_probs


class ProbabilityProvider(Protocol):
    """Source of model probabilities for outcomes (home/draw/away)."""

    @property
    def model_version(self) -> str: ...

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]: ...


def _runner_quotes(
    bundle: MarketSnapshotBundle, runners: list[Runner]
) -> dict[int, float | None]:
    """Map runner_id → best_back_price (float or None) using the bundle's snapshot."""
    bundle_by_id = {rs.runner_id: rs for rs in bundle.runners}
    out: dict[int, float | None] = {}
    for r in runners:
        snap = bundle_by_id.get(r.runner_id)
        if snap is None or snap.best_back_price is None:
            out[r.runner_id] = None
        else:
            out[r.runner_id] = float(snap.best_back_price)
    return out


class MarketImpliedProvider:
    """Returns the market-implied probabilities (no edge by construction).

    Useful for sanity tests: every outcome's edge_gross is exactly 0.
    """

    model_version = "STUB_MARKET_IMPLIED_V1"

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]:
        return compute_market_probs(_runner_quotes(bundle, runners))


class BiasedStubProvider:
    """Market-implied + bias on the home runner (sort_priority=1).

    `home_bias` is added to the home prob; the same total is subtracted
    proportionally from the other runners. Result is renormalized to sum to 1.0.
    """

    model_version = "STUB_BIAS_V1"

    def __init__(self, home_bias: float = 0.05):
        self.home_bias = home_bias

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> dict[int, float]:
        market_probs = compute_market_probs(_runner_quotes(bundle, runners))

        # Identify home runner: smallest non-None sort_priority
        sorted_runners = sorted(
            runners, key=lambda r: (r.sort_priority is None, r.sort_priority)
        )
        if not sorted_runners:
            return market_probs
        home_id = sorted_runners[0].runner_id

        n_others = max(1, len(market_probs) - 1)
        biased: dict[int, float] = {}
        for rid, p in market_probs.items():
            if rid == home_id:
                biased[rid] = p + self.home_bias
            else:
                biased[rid] = max(0.0, p - self.home_bias / n_others)

        total = sum(biased.values())
        if total <= 0:
            return market_probs
        return {rid: p / total for rid, p in biased.items()}

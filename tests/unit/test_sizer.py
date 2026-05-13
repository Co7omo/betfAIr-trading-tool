"""Unit tests for the pure Kelly sizer."""

import math
from decimal import Decimal

from betfair_trading.services.sizer import compute_stake, kelly_fraction


def test_kelly_fraction_positive_edge():
    # p=0.55, o=2.0 → (0.55*2 - 1) / (2 - 1) = 0.10
    assert math.isclose(kelly_fraction(0.55, 2.0), 0.10, abs_tol=1e-9)


def test_kelly_fraction_zero_when_negative_edge():
    # p=0.40, o=2.0 → negative → clamp to 0
    assert kelly_fraction(0.40, 2.0) == 0.0


def test_kelly_fraction_zero_when_odds_le_one():
    assert kelly_fraction(0.5, 1.0) == 0.0
    assert kelly_fraction(0.5, 0.5) == 0.0


def test_compute_stake_capped_at_max_fraction():
    # Without cap: 1000 * 0.25 * 0.10 = 25.0
    # With cap: 1000 * 0.02 = 20.0
    stake = compute_stake(
        bankroll=1000.0, p_model=0.55, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake == Decimal("20.00")


def test_compute_stake_below_min_returns_none():
    # Bankroll=10, kelly_mult=0.25, p=0.55, o=2.0
    # raw = 10 * 0.25 * 0.10 = 0.25 → below min_stake=2.0
    stake = compute_stake(
        bankroll=10.0, p_model=0.55, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake is None


def test_compute_stake_zero_kelly_returns_none():
    # Negative edge → kelly_fraction=0 → stake=0 → < min → None
    stake = compute_stake(
        bankroll=1000.0, p_model=0.40, odds=2.0,
        kelly_multiplier=0.25, max_stake_fraction=0.02, min_stake=2.0,
    )
    assert stake is None

"""Pure Kelly sizing math: fractional Kelly with cap and minimum stake."""

from decimal import Decimal


def kelly_fraction(p_model: float, odds: float) -> float:
    """f* = (p*o - 1) / (o - 1).

    Returns 0.0 if odds <= 1.0 (no payoff) or if f* < 0 (no edge → no trade).
    """
    if odds <= 1.0:
        return 0.0
    f_star = (p_model * odds - 1.0) / (odds - 1.0)
    return max(0.0, f_star)


def compute_stake(
    bankroll: float,
    p_model: float,
    odds: float,
    kelly_multiplier: float = 0.25,
    max_stake_fraction: float = 0.02,
    min_stake: float = 2.0,
) -> Decimal | None:
    """Compute the stake to place.

    raw_stake  = bankroll * kelly_multiplier * kelly_fraction(p_model, odds)
    capped     = min(raw_stake, bankroll * max_stake_fraction)
    Returns Decimal(rounded 2dp) if capped >= min_stake, else None.
    """
    f = kelly_fraction(p_model, odds)
    raw_stake = bankroll * kelly_multiplier * f
    cap = bankroll * max_stake_fraction
    capped = min(raw_stake, cap)
    if capped < min_stake:
        return None
    return Decimal(str(round(capped, 2)))

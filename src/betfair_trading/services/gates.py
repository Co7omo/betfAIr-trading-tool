"""Pure risk-gate predicates for the Decision Engine.

Each gate returns (passed: bool, reason: str). Reason is "ok" on pass,
descriptive on fail.
"""

from decimal import Decimal


def check_kill_switch(active: bool) -> tuple[bool, str]:
    if active:
        return False, "kill_switch_active"
    return True, "ok"


def check_window(
    minutes_to_start: float, window_start_min: int, window_end_min: int
) -> tuple[bool, str]:
    """Pass when minutes_to_start ∈ [window_end_min, window_start_min]."""
    if minutes_to_start > window_start_min:
        return False, "outside_window_too_far"
    if minutes_to_start < window_end_min:
        return False, "outside_window_too_close"
    return True, "ok"


def check_edge_threshold(edge_net: float, threshold: float) -> tuple[bool, str]:
    if edge_net < threshold:
        return False, "edge_below_threshold"
    return True, "ok"


def check_liquidity(
    best_back_size: Decimal | None, min_liquidity: float
) -> tuple[bool, str]:
    if best_back_size is None:
        return False, "size_missing"
    if float(best_back_size) < min_liquidity:
        return False, "size_below_min"
    return True, "ok"


def check_spread(spread: Decimal | None, max_spread: float) -> tuple[bool, str]:
    if spread is None:
        return False, "spread_missing"
    if float(spread) > max_spread:
        return False, "spread_above_max"
    return True, "ok"


def check_position_limit(allow_count: int, max_per_event: int) -> tuple[bool, str]:
    if allow_count >= max_per_event:
        return False, "position_limit_reached"
    return True, "ok"


def check_daily_drawdown(
    current_dd_fraction: float, max_dd_fraction: float
) -> tuple[bool, str]:
    """Phase 2: current_dd_fraction is hardcoded 0.0 by callers until P&L exists."""
    if current_dd_fraction >= max_dd_fraction:
        return False, "daily_drawdown_reached"
    return True, "ok"

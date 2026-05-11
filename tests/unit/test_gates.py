"""Unit tests for risk gate predicates."""

from decimal import Decimal

from betfair_trading.services.gates import (
    check_daily_drawdown,
    check_edge_threshold,
    check_kill_switch,
    check_liquidity,
    check_position_limit,
    check_spread,
    check_window,
)


def test_check_kill_switch_inactive_passes():
    passed, reason = check_kill_switch(active=False)
    assert passed is True
    assert reason == "ok"


def test_check_kill_switch_active_fails():
    passed, reason = check_kill_switch(active=True)
    assert passed is False
    assert reason == "kill_switch_active"


def test_check_window_in_range_passes():
    passed, reason = check_window(minutes_to_start=60.0, window_start_min=120, window_end_min=10)
    assert passed is True


def test_check_window_too_far_fails():
    passed, reason = check_window(minutes_to_start=200.0, window_start_min=120, window_end_min=10)
    assert passed is False
    assert "too_far" in reason or "outside" in reason


def test_check_window_too_close_fails():
    passed, reason = check_window(minutes_to_start=5.0, window_start_min=120, window_end_min=10)
    assert passed is False


def test_check_edge_threshold_above_passes():
    passed, _ = check_edge_threshold(edge_net=0.025, threshold=0.02)
    assert passed is True


def test_check_edge_threshold_below_fails():
    passed, reason = check_edge_threshold(edge_net=0.01, threshold=0.02)
    assert passed is False
    assert "below" in reason


def test_check_liquidity_above_passes():
    passed, _ = check_liquidity(best_back_size=Decimal("200"), min_liquidity=100.0)
    assert passed is True


def test_check_liquidity_below_fails():
    passed, reason = check_liquidity(best_back_size=Decimal("50"), min_liquidity=100.0)
    assert passed is False


def test_check_liquidity_none_fails():
    passed, _ = check_liquidity(best_back_size=None, min_liquidity=100.0)
    assert passed is False


def test_check_spread_below_passes():
    passed, _ = check_spread(spread=Decimal("0.04"), max_spread=0.10)
    assert passed is True


def test_check_spread_above_fails():
    passed, reason = check_spread(spread=Decimal("0.50"), max_spread=0.10)
    assert passed is False


def test_check_spread_none_fails():
    passed, _ = check_spread(spread=None, max_spread=0.10)
    assert passed is False


def test_check_position_limit_under_cap_passes():
    passed, _ = check_position_limit(allow_count=0, max_per_event=1)
    assert passed is True


def test_check_position_limit_at_cap_fails():
    passed, reason = check_position_limit(allow_count=1, max_per_event=1)
    assert passed is False


def test_check_daily_drawdown_under_limit_passes():
    passed, _ = check_daily_drawdown(current_dd_fraction=0.02, max_dd_fraction=0.05)
    assert passed is True


def test_check_daily_drawdown_at_limit_fails():
    passed, reason = check_daily_drawdown(current_dd_fraction=0.05, max_dd_fraction=0.05)
    assert passed is False

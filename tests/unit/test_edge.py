"""Unit tests for pure edge math functions."""

import math

from betfair_trading.services.edge import compute_market_probs, compute_net_edge


def test_market_probs_normalize_to_one():
    quotes = {101: 2.0, 102: 4.0, 103: 4.0}  # implied: 0.5, 0.25, 0.25
    probs = compute_market_probs(quotes)
    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert math.isclose(probs[101], 0.5, abs_tol=1e-9)
    assert math.isclose(probs[102], 0.25, abs_tol=1e-9)
    assert math.isclose(probs[103], 0.25, abs_tol=1e-9)


def test_market_probs_handles_overround():
    """Real markets have overround: sum of 1/odds > 1.0. Normalization fixes it."""
    quotes = {101: 1.9, 102: 3.5, 103: 4.0}  # implied sum > 1
    probs = compute_market_probs(quotes)
    total = sum(probs.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_market_probs_skips_missing_quote():
    quotes = {101: 2.0, 102: None, 103: 4.0}
    probs = compute_market_probs(quotes)
    assert probs[102] == 0.0
    # Sum across the two with valid quotes should be 1.0
    assert math.isclose(probs[101] + probs[103], 1.0, abs_tol=1e-9)


def test_market_probs_skips_zero_or_negative():
    quotes = {101: 2.0, 102: 0.0, 103: -1.0}
    probs = compute_market_probs(quotes)
    assert probs[101] == 1.0
    assert probs[102] == 0.0
    assert probs[103] == 0.0


def test_net_edge_with_zero_commission():
    gross, net = compute_net_edge(p_model=0.55, p_market=0.50, commission_rate=0.0)
    assert math.isclose(gross, 0.05, abs_tol=1e-9)
    assert math.isclose(net, 0.05, abs_tol=1e-9)


def test_net_edge_with_default_commission():
    gross, net = compute_net_edge(p_model=0.55, p_market=0.50, commission_rate=0.05)
    assert math.isclose(gross, 0.05, abs_tol=1e-9)
    # net = 0.55 * 0.95 - 0.50 = 0.5225 - 0.50 = 0.0225
    assert math.isclose(net, 0.0225, abs_tol=1e-9)


def test_net_edge_negative_when_p_model_below_market():
    gross, net = compute_net_edge(p_model=0.40, p_market=0.50, commission_rate=0.05)
    assert gross < 0
    assert net < 0

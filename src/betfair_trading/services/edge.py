"""Pure edge math: market-implied probabilities and net edge calculation."""


def compute_market_probs(runner_quotes: dict[int, float | None]) -> dict[int, float]:
    """Normalize 1/odds_i across runners with valid quotes.

    Args:
        runner_quotes: {runner_id: best_back_price}.

    Returns:
        {runner_id: market-implied probability}. Sum across runners with valid
        positive quotes equals 1.0; runners with None or non-positive prices
        get prob=0.0.
    """
    raw = {}
    for rid, price in runner_quotes.items():
        if price is None or price <= 0:
            raw[rid] = 0.0
        else:
            raw[rid] = 1.0 / price

    total = sum(raw.values())
    if total <= 0:
        return {rid: 0.0 for rid in runner_quotes}

    return {rid: p / total for rid, p in raw.items()}


def compute_net_edge(
    p_model: float, p_market: float, commission_rate: float = 0.05
) -> tuple[float, float]:
    """Compute (edge_gross, edge_net) for a single outcome.

    edge_gross = p_model - p_market
    edge_net   = p_model * (1 - commission_rate) - p_market

    Args:
        p_model: model probability of this outcome winning.
        p_market: market-implied probability of this outcome winning.
        commission_rate: fractional commission on winnings (Betfair default 0.05).

    Returns:
        (edge_gross, edge_net) as floats.
    """
    edge_gross = p_model - p_market
    edge_net = p_model * (1.0 - commission_rate) - p_market
    return edge_gross, edge_net

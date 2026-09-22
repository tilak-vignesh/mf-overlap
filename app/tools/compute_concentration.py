import uuid
from collections import defaultdict


def compute_concentration(
    fund_holdings: dict[uuid.UUID, dict[uuid.UUID, float]], fund_portfolio_weights: dict[uuid.UUID, float]
) -> dict[uuid.UUID, float]:
    """Aggregate weighted exposure per stock across a whole portfolio of funds.

    fund_holdings: fund_id -> {stock_id: weight_in_fund}
    fund_portfolio_weights: fund_id -> weight of that fund within the user's portfolio
    """
    exposure: dict[uuid.UUID, float] = defaultdict(float)
    for fund_id, holdings in fund_holdings.items():
        portfolio_weight = fund_portfolio_weights[fund_id]
        for stock_id, weight in holdings.items():
            exposure[stock_id] += portfolio_weight * weight
    return dict(exposure)

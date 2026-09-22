import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fund import Fund
from app.models.stock import Stock
from app.tools.compute_concentration import compute_concentration
from app.tools.compute_overlap import compute_overlap
from app.tools.fetch_holdings import fetch_holdings_many
from app.tools.search_fund import search_fund as _search_fund

TOP_N_STOCKS = 10


async def search_fund(session: AsyncSession, query: str) -> dict:
    matches = await _search_fund(query, session)
    return {
        "candidates": [
            {"fund_id": str(fund_id), "name": name, "match_score": round(score, 1)}
            for fund_id, name, score in matches
        ]
    }


async def get_fund_overlap(session: AsyncSession, fund_id_a: str, fund_id_b: str) -> dict:
    fund_a = await session.get(Fund, uuid.UUID(fund_id_a))
    fund_b = await session.get(Fund, uuid.UUID(fund_id_b))
    if fund_a is None or fund_b is None:
        return {"error": "unknown fund_id — call search_fund first to resolve fund names to ids"}

    holdings = await fetch_holdings_many([fund_a.fund_id, fund_b.fund_id], session)
    holdings_a = holdings[fund_a.fund_id]
    holdings_b = holdings[fund_b.fund_id]
    weights_a = {h.stock_id: float(h.weight) for h in holdings_a}
    weights_b = {h.stock_id: float(h.weight) for h in holdings_b}

    overlap_pct = compute_overlap(weights_a, weights_b)

    common_ids = weights_a.keys() & weights_b.keys()
    top_common = sorted(common_ids, key=lambda s: min(weights_a[s], weights_b[s]), reverse=True)[:5]
    stocks = await _stocks_by_id(session, top_common)

    return {
        "fund_a_name": fund_a.name,
        "fund_b_name": fund_b.name,
        "overlap_pct": round(overlap_pct, 2),
        "as_of_date_a": str(holdings_a[0].as_of_date) if holdings_a else None,
        "as_of_date_b": str(holdings_b[0].as_of_date) if holdings_b else None,
        "top_common_stocks": [
            {
                "name": stocks[s].name,
                "weight_in_fund_a_pct": round(weights_a[s], 2),
                "weight_in_fund_b_pct": round(weights_b[s], 2),
            }
            for s in top_common
        ],
    }


async def get_portfolio_concentration(session: AsyncSession, fund_ids: list[str], weights: list[float]) -> dict:
    if len(fund_ids) != len(weights):
        return {"error": "fund_ids and weights must be the same length"}

    fund_uuids = [uuid.UUID(f) for f in fund_ids]
    fund_names: dict[uuid.UUID, str] = {}
    fund_portfolio_weights: dict[uuid.UUID, float] = {}

    for fund_id, weight in zip(fund_uuids, weights):
        fund = await session.get(Fund, fund_id)
        if fund is None:
            return {"error": f"unknown fund_id {fund_id} — call search_fund first to resolve fund names to ids"}
        fund_names[fund_id] = fund.name
        fund_portfolio_weights[fund_id] = weight

    holdings_by_fund = await fetch_holdings_many(fund_uuids, session)
    fund_holdings: dict[uuid.UUID, dict[uuid.UUID, float]] = {
        fund_id: {h.stock_id: float(h.weight) for h in holdings} for fund_id, holdings in holdings_by_fund.items()
    }

    exposure = compute_concentration(fund_holdings, fund_portfolio_weights)
    top_stock_ids = sorted(exposure, key=exposure.get, reverse=True)[:TOP_N_STOCKS]
    stocks = await _stocks_by_id(session, top_stock_ids)

    return {
        "funds": [
            {"fund_id": str(fund_id), "name": fund_names[fund_id], "portfolio_weight": weight}
            for fund_id, weight in zip(fund_uuids, weights)
        ],
        "top_exposures": [
            {"stock_name": stocks[s].name, "portfolio_exposure_pct": round(exposure[s], 2)} for s in top_stock_ids
        ],
    }


async def _stocks_by_id(session: AsyncSession, stock_ids) -> dict[uuid.UUID, Stock]:
    stock_ids = list(stock_ids)
    if not stock_ids:
        return {}
    result = await session.execute(select(Stock).where(Stock.stock_id.in_(stock_ids)))
    return {s.stock_id: s for s in result.scalars()}

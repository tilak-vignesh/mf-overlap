import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fund import Fund
from app.tools.groww_lookup import find_asset_allocation

# Groww's payload has no dedicated "gold" field. "commodities" is the closest
# proxy (gold ETF/fund-of-fund exposure typically lands there) but this is an
# approximation, not a verified gold-specific figure — callers should say so.
_KNOWN_KEYS = {"equity", "debt", "commodities"}


async def fetch_asset_allocation(fund_id: uuid.UUID, session: AsyncSession) -> dict:
    """Live equity/debt/gold-or-commodity/other split for a fund, sourced from
    Groww's portfolio-stats endpoint (a separate endpoint from the one
    `fetch_holdings` uses, not just a different field of the same payload —
    see groww_lookup.find_asset_allocation). Not cached (unlike holdings) —
    a cheap, low-volume call. Raises ValueError for an unknown fund_id, or
    groww_lookup.FundLookupError if the fund can't be found/resolved on Groww.
    """
    fund = await session.get(Fund, fund_id)
    if fund is None:
        raise ValueError(f"no fund with id {fund_id}")

    result = await find_asset_allocation(fund.name)
    raw: dict = result.get("asset_allocation") or {}

    other_pct = sum(v for k, v in raw.items() if k not in _KNOWN_KEYS)

    return {
        "fund_name": fund.name,
        "equity_pct": raw.get("equity", 0.0),
        "debt_pct": raw.get("debt", 0.0),
        "gold_commodity_pct": raw.get("commodities", 0.0),
        "other_pct": round(other_pct, 4),
    }

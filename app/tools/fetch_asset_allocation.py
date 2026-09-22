import asyncio
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
    For more than one fund, prefer fetch_asset_allocation_many — it fetches
    concurrently instead of one at a time."""
    fund = await session.get(Fund, fund_id)
    if fund is None:
        raise ValueError(f"no fund with id {fund_id}")
    return await _fetch(fund.name)


async def fetch_asset_allocation_many(
    fund_ids: list[uuid.UUID], session: AsyncSession
) -> tuple[dict[uuid.UUID, dict], dict[uuid.UUID, str]]:
    """Same data as fetch_asset_allocation, for several funds at once —
    with graceful per-fund degradation rather than fetch_holdings_many's
    fail-the-whole-batch contract, since callers here (allocation-gap
    analysis) want partial results plus a note about what's missing rather
    than an all-or-nothing failure. Fund-name lookups run sequentially (they
    share one AsyncSession, unsafe for concurrent use) — fast local reads,
    not the bottleneck. The actual live Groww lookups run concurrently
    across every fund. Returns (results, errors) — a fund_id lands in
    exactly one of the two."""
    fund_names: dict[uuid.UUID, str] = {}
    errors: dict[uuid.UUID, str] = {}
    for fund_id in fund_ids:
        fund = await session.get(Fund, fund_id)
        if fund is None:
            errors[fund_id] = f"no fund with id {fund_id}"
        else:
            fund_names[fund_id] = fund.name

    fetched = await asyncio.gather(*(_fetch(name) for name in fund_names.values()), return_exceptions=True)

    results: dict[uuid.UUID, dict] = {}
    for fund_id, result in zip(fund_names.keys(), fetched):
        if isinstance(result, Exception):
            errors[fund_id] = str(result)
        else:
            results[fund_id] = result
    return results, errors


async def _fetch(fund_name: str) -> dict:
    result = await find_asset_allocation(fund_name)
    raw: dict = result.get("asset_allocation") or {}
    other_pct = sum(v for k, v in raw.items() if k not in _KNOWN_KEYS)

    return {
        "fund_name": fund_name,
        "equity_pct": raw.get("equity", 0.0),
        "debt_pct": raw.get("debt", 0.0),
        "gold_commodity_pct": raw.get("commodities", 0.0),
        "other_pct": round(other_pct, 4),
    }

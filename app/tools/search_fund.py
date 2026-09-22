import uuid

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.groww_client import GrowwClient
from app.tools.upsert import get_or_create_fund


async def search_fund(query: str, session: AsyncSession, size: int = 6) -> list[tuple[uuid.UUID, str, float]]:
    """Resolve an ambiguous/misspelled fund name typed by the user.

    Searches Groww's live fund index, upserts each candidate into `funds`
    (fund_id, name, amc, isin), and returns (fund_id, name, score) tuples
    ranked by fuzzy match quality against `query`.
    """
    async with GrowwClient() as client:
        candidates = await client.search_schemes(query, size=size)

        results: list[tuple[uuid.UUID, str, float]] = []
        for candidate in candidates:
            detail = await client.scheme_detail(candidate["search_id"])
            isin = detail.get("isin")
            if not isin:
                continue
            fund = await get_or_create_fund(
                session,
                isin=isin,
                name=detail["scheme_name"],
                amc=detail.get("fund_house") or detail.get("amc") or "",
            )
            score = fuzz.token_set_ratio(query, detail["scheme_name"])
            results.append((fund.fund_id, detail["scheme_name"], score))

        results.sort(key=lambda r: r[2], reverse=True)
        return results

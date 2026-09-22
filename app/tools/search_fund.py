import asyncio
import uuid

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.groww_client import get_shared_client
from app.tools.upsert import get_or_create_fund


async def search_fund(query: str, session: AsyncSession, size: int = 6) -> list[tuple[uuid.UUID, str, float]]:
    """Resolve an ambiguous/misspelled fund name typed by the user.

    Searches Groww's live fund index, upserts each candidate into `funds`
    (fund_id, name, amc, isin), and returns (fund_id, name, score) tuples
    ranked by fuzzy match quality against `query`.
    """
    client = get_shared_client()
    candidates = await client.search_schemes(query, size=size)
    if not candidates:
        return []

    # Fetching each candidate's full detail is the slow, independent part
    # (a separate network round-trip per candidate) — run them
    # concurrently. DB upserts still happen sequentially below, since
    # they share one AsyncSession, which isn't safe for concurrent use.
    details = await asyncio.gather(
        *(client.scheme_detail(c["search_id"]) for c in candidates),
        return_exceptions=True,
    )

    results: list[tuple[uuid.UUID, str, float]] = []
    for detail in details:
        if isinstance(detail, Exception):
            continue
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

import re

from rapidfuzz import fuzz

from app.tools.groww_client import GrowwClient, get_shared_client

MIN_MATCH_SCORE = 60

# Groww's search index tends to return nothing for a query that includes the
# plan/option suffix (e.g. "... Direct Growth") even though the scheme exists,
# so strip it before searching.
_PLAN_SUFFIX_RE = re.compile(
    r"\b(direct|regular)?\s*(growth|idcw|dividend|payout|reinvestment|plan)\b", re.IGNORECASE
)


def _search_query(fund_name: str) -> str:
    stripped = _PLAN_SUFFIX_RE.sub("", fund_name)
    return re.sub(r"\s+", " ", stripped).strip() or fund_name


class FundLookupError(Exception):
    pass


async def _find_best_candidate(client: GrowwClient, fund_name: str, min_match_score: int) -> dict:
    try:
        candidates = await client.search_schemes(_search_query(fund_name), size=6)
    except Exception as exc:
        raise FundLookupError(f"search request failed: {exc}") from exc

    if not candidates:
        raise FundLookupError("fund not found")

    best = max(candidates, key=lambda c: fuzz.token_set_ratio(fund_name, c["title"]))
    if fuzz.token_set_ratio(fund_name, best["title"]) < min_match_score:
        raise FundLookupError(f"no confident name match for '{fund_name}'")

    return best


async def find_scheme_detail(fund_name: str, min_match_score: int = MIN_MATCH_SCORE) -> dict:
    """Search Groww by name, fuzzy-match the best candidate, and return its
    full scheme detail payload (metadata + holdings). Raises FundLookupError
    with a human-readable reason on any failure."""
    client = get_shared_client()
    best = await _find_best_candidate(client, fund_name, min_match_score)
    try:
        return await client.scheme_detail(best["search_id"])
    except Exception as exc:
        raise FundLookupError(f"detail request failed: {exc}") from exc


async def find_asset_allocation(fund_name: str, min_match_score: int = MIN_MATCH_SCORE) -> dict:
    """Search Groww by name, fuzzy-match the best candidate, and return its
    equity/debt/commodities/... asset allocation breakdown. This is a
    genuinely separate endpoint from scheme_detail's holdings (portfolio
    stats, keyed by the fund's numeric scheme_code, not its slug) — not just
    a different field of the same payload. Raises FundLookupError with a
    human-readable reason on any failure."""
    client = get_shared_client()
    best = await _find_best_candidate(client, fund_name, min_match_score)
    try:
        detail = await client.scheme_detail(best["search_id"])
    except Exception as exc:
        raise FundLookupError(f"detail request failed: {exc}") from exc

    scheme_code = detail.get("scheme_code")
    if not scheme_code:
        raise FundLookupError("no scheme_code in detail response")

    try:
        stats = await client.portfolio_stats(scheme_code, best["search_id"])
    except Exception as exc:
        raise FundLookupError(f"portfolio stats request failed: {exc}") from exc

    return {
        "scheme_name": detail.get("scheme_name"),
        "asset_allocation": stats.get("asset_allocation") or {},
    }

import re

from rapidfuzz import fuzz

from app.tools.groww_client import GrowwClient
from app.tools.scrapers.base import ScrapedHolding, ScrapedHoldings, ScrapeFailure

MIN_MATCH_SCORE = 60
MIN_HOLDINGS_COVERAGE_PCT = 70.0

# Groww's search index tends to return nothing for a query that includes the
# plan/option suffix (e.g. "... Direct Growth") even though the scheme exists,
# so strip it before searching.
_PLAN_SUFFIX_RE = re.compile(
    r"\b(direct|regular)?\s*(growth|idcw|dividend|payout|reinvestment|plan)\b", re.IGNORECASE
)


def _search_query(fund_name: str) -> str:
    stripped = _PLAN_SUFFIX_RE.sub("", fund_name)
    return re.sub(r"\s+", " ", stripped).strip() or fund_name


class GrowwScraper:
    name = "groww"

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure:
        async with GrowwClient() as client:
            try:
                candidates = await client.search_schemes(_search_query(fund_name), size=6)
            except Exception as exc:
                return ScrapeFailure(source=self.name, reason=f"search request failed: {exc}")

            if not candidates:
                return ScrapeFailure(source=self.name, reason="fund not found")

            best = max(candidates, key=lambda c: fuzz.token_set_ratio(fund_name, c["title"]))
            if fuzz.token_set_ratio(fund_name, best["title"]) < MIN_MATCH_SCORE:
                return ScrapeFailure(source=self.name, reason=f"no confident name match for '{fund_name}'")

            try:
                detail = await client.scheme_detail(best["search_id"])
            except Exception as exc:
                return ScrapeFailure(source=self.name, reason=f"detail request failed: {exc}")

            raw_holdings = detail.get("holdings") or []
            if not raw_holdings:
                return ScrapeFailure(source=self.name, reason="no holdings in response")

            total_weight = sum(h.get("corpus_per") or 0 for h in raw_holdings)
            if total_weight < MIN_HOLDINGS_COVERAGE_PCT:
                return ScrapeFailure(
                    source=self.name,
                    reason=f"incomplete: holdings sum to {total_weight:.1f}% of AUM",
                )

            holdings = [
                ScrapedHolding(
                    name=h["company_name"],
                    weight=h["corpus_per"],
                    external_id=h["stock_search_id"],
                )
                for h in raw_holdings
                if h.get("stock_search_id") and h.get("corpus_per") is not None
            ]

            # Each holding row carries its own portfolio_date (when the disclosed
            # holdings composition is actually as-of); that's a different, usually
            # much older date than the fund-level nav_date, which is just today's
            # price date. All rows in one scrape share the same portfolio_date.
            as_of_date = raw_holdings[0].get("portfolio_date", "")

            return ScrapedHoldings(
                fund_name=detail["scheme_name"],
                as_of_date=as_of_date,
                holdings=holdings,
                source=self.name,
            )

from app.tools.groww_lookup import FundLookupError, find_scheme_detail
from app.tools.scrapers.base import ScrapedHolding, ScrapedHoldings, ScrapeFailure

MIN_HOLDINGS_COVERAGE_PCT = 70.0


class GrowwScraper:
    name = "groww"

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure:
        try:
            detail = await find_scheme_detail(fund_name)
        except FundLookupError as exc:
            return ScrapeFailure(source=self.name, reason=str(exc))

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

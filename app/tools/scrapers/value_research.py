from app.tools.scrapers.base import ScrapedHoldings, ScrapeFailure


class ValueResearchScraper:
    name = "value_research"

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure:
        raise NotImplementedError

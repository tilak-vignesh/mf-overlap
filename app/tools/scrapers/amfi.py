from app.tools.scrapers.base import ScrapedHoldings, ScrapeFailure


class AmfiScraper:
    name = "amfi"

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure:
        raise NotImplementedError

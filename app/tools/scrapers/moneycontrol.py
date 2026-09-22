from app.tools.scrapers.base import ScrapedHoldings, ScrapeFailure


class MoneycontrolScraper:
    name = "moneycontrol"

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure:
        raise NotImplementedError

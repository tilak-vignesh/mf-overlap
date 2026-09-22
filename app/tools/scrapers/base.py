from dataclasses import dataclass
from typing import Protocol


@dataclass
class ScrapedHolding:
    name: str
    weight: float
    external_id: str  # source-specific stock key, used when the source doesn't give ISIN
    isin: str | None = None
    ticker: str | None = None


@dataclass
class ScrapedHoldings:
    fund_name: str
    as_of_date: str
    holdings: list[ScrapedHolding]
    source: str


@dataclass
class ScrapeFailure:
    source: str
    reason: str  # e.g. "timeout", "layout changed", "fund not found", "incomplete: sums to 42% of AUM"


class Scraper(Protocol):
    name: str

    async def fetch(self, fund_name: str) -> ScrapedHoldings | ScrapeFailure: ...

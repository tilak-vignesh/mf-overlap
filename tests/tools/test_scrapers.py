import pytest

from app.tools.scrapers.base import ScrapeFailure
from app.tools.scrapers.groww import GrowwScraper


class _FakeGrowwClient:
    def __init__(self, candidates, detail):
        self._candidates = candidates
        self._detail = detail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def search_schemes(self, query, size=6):
        return self._candidates

    async def scheme_detail(self, search_id):
        return self._detail


@pytest.mark.asyncio
async def test_fetch_reports_incomplete_holdings(monkeypatch):
    candidates = [{"title": "Test Flexi Cap Fund", "search_id": "test-flexi-cap-fund"}]
    detail = {
        "scheme_name": "Test Flexi Cap Fund",
        "nav_date": "01-Jan-2026",
        "holdings": [{"company_name": "Foo Ltd", "corpus_per": 10.0, "stock_search_id": "foo-ltd"}],
    }
    monkeypatch.setattr(
        "app.tools.scrapers.groww.GrowwClient",
        lambda: _FakeGrowwClient(candidates, detail),
    )

    result = await GrowwScraper().fetch("Test Flexi Cap Fund")

    assert isinstance(result, ScrapeFailure)
    assert "incomplete" in result.reason


@pytest.mark.asyncio
async def test_fetch_reports_no_match(monkeypatch):
    monkeypatch.setattr(
        "app.tools.scrapers.groww.GrowwClient",
        lambda: _FakeGrowwClient([], {}),
    )

    result = await GrowwScraper().fetch("Some Fund")

    assert isinstance(result, ScrapeFailure)
    assert result.reason == "fund not found"


@pytest.mark.skip(reason="hits the live Groww API; run manually to verify against real data")
@pytest.mark.asyncio
async def test_fetch_real_fund_live():
    result = await GrowwScraper().fetch("Parag Parikh Flexi Cap Fund")
    assert not isinstance(result, ScrapeFailure)
    assert len(result.holdings) > 0

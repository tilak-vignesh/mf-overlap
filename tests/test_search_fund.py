import pytest

from app.tools.search_fund import search_fund


@pytest.mark.skip(reason="hits the live Groww API and a real DB session; run manually to verify against real data")
@pytest.mark.asyncio
async def test_search_fund_live(db_session):
    results = await search_fund("Parag Parikh Flexi Cap", db_session)
    assert results
    assert any("Parag Parikh" in name for _, name, _ in results)

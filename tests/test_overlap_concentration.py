import pytest

from app.agent.domains import overlap_concentration
from app.agent.domains.overlap_concentration import TOOLS, dispatch


def test_tool_schemas_are_valid_shape():
    names = {t["name"] for t in TOOLS}
    assert names == {"search_fund", "get_fund_overlap", "get_portfolio_concentration"}
    for tool in TOOLS:
        assert set(tool["parameters"]["required"]) <= set(tool["parameters"]["properties"])


@pytest.mark.asyncio
async def test_dispatch_routes_to_search_fund(monkeypatch):
    calls = {}

    async def fake_search_fund(session, query):
        calls["query"] = query
        return {"candidates": [{"fund_id": "x", "name": "Test Fund", "match_score": 90.0}]}

    # `dispatch` closed over the _HANDLERS dict object at import time, so the
    # module attribute must be mutated in place, not reassigned.
    monkeypatch.setitem(overlap_concentration._HANDLERS, "search_fund", fake_search_fund)

    result = await dispatch("search_fund", {"query": "test fund"}, session=None)

    assert calls["query"] == "test fund"
    assert result["candidates"][0]["name"] == "Test Fund"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error():
    result = await dispatch("not_a_real_tool", {}, session=None)
    assert "error" in result


@pytest.mark.skip(reason="hits the live Gemini API; needs GEMINI_API_KEY, run manually")
@pytest.mark.asyncio
async def test_run_live(db_session):
    from app.agent.domains.overlap_concentration import run

    answer = await run("What funds are similar to Parag Parikh Flexi Cap Fund?", db_session)
    assert isinstance(answer, str) and answer

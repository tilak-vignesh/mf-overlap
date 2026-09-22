import pytest

from app.agent import orchestrator
from app.agent.orchestrator import TOOLS, dispatch


def test_coordinator_tool_schemas_are_valid_shape():
    names = {t["name"] for t in TOOLS}
    assert names == {"consult_overlap_agent", "consult_allocation_gap_agent"}
    for tool in TOOLS:
        assert set(tool["parameters"]["required"]) <= set(tool["parameters"]["properties"])


@pytest.mark.asyncio
async def test_dispatch_routes_to_overlap_agent(monkeypatch):
    calls = {}

    async def fake_run(query, session):
        calls["query"] = query
        return "42% overlap"

    monkeypatch.setattr("app.agent.domains.overlap_concentration.run", fake_run)

    result = await dispatch("consult_overlap_agent", {"query": "compare fund A and B"}, session=None)

    assert calls["query"] == "compare fund A and B"
    assert result == {"answer": "42% overlap"}


@pytest.mark.asyncio
async def test_dispatch_routes_to_allocation_gap_agent(monkeypatch):
    calls = {}

    async def fake_run(query, session):
        calls["query"] = query
        return "you're overweight equity by 15%"

    monkeypatch.setattr("app.agent.domains.allocation_gap.run", fake_run)

    result = await dispatch(
        "consult_allocation_gap_agent", {"query": "am I on target for 60/30/10?"}, session=None
    )

    assert calls["query"] == "am I on target for 60/30/10?"
    assert result == {"answer": "you're overweight equity by 15%"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error():
    result = await dispatch("not_a_real_tool", {}, session=None)
    assert "error" in result


@pytest.mark.skip(reason="hits the live Gemini API; needs GEMINI_API_KEY, run manually")
@pytest.mark.asyncio
async def test_run_agent_live(db_session):
    reply, history = await orchestrator.run_agent("What funds are similar to Parag Parikh Flexi Cap Fund?", db_session)
    assert isinstance(reply, str) and reply
    assert history[-1] == {"role": "model", "text": reply}

    # continuing the conversation with the returned history
    reply2, history2 = await orchestrator.run_agent(
        "And what about HDFC Balanced Advantage?", db_session, history=history
    )
    assert isinstance(reply2, str) and reply2
    assert len(history2) == len(history) + 2

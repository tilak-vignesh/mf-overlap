import uuid

import pytest

from app.agent.domains import allocation_gap
from app.agent.domains.allocation_gap import TOOLS, dispatch
from app.tools.groww_lookup import FundLookupError


def test_tool_schemas_are_valid_shape():
    names = {t["name"] for t in TOOLS}
    assert names == {"search_fund", "get_allocation_gap"}
    for tool in TOOLS:
        assert set(tool["parameters"]["required"]) <= set(tool["parameters"]["properties"])


@pytest.mark.asyncio
async def test_get_allocation_gap_blends_and_computes_gap(monkeypatch):
    fund_a, fund_b = str(uuid.uuid4()), str(uuid.uuid4())

    async def fake_fetch(fund_id, session):
        if str(fund_id) == fund_a:
            return {"fund_name": "Fund A", "equity_pct": 90.0, "debt_pct": 5.0, "gold_commodity_pct": 0.0, "other_pct": 5.0}
        return {"fund_name": "Fund B", "equity_pct": 10.0, "debt_pct": 80.0, "gold_commodity_pct": 5.0, "other_pct": 5.0}

    monkeypatch.setattr("app.agent.domains.allocation_gap.fetch_asset_allocation", fake_fetch)

    result = await dispatch(
        "get_allocation_gap",
        {
            "fund_ids": [fund_a, fund_b],
            "weights": [0.5, 0.5],
            "target_equity_pct": 50.0,
            "target_debt_pct": 40.0,
            "target_gold_pct": 10.0,
        },
        session=None,
    )

    assert result["current"] == {"equity_pct": 50.0, "debt_pct": 42.5, "gold_commodity_pct": 2.5, "other_pct": 5.0}
    assert result["gap"] == {"equity_pct": 0.0, "debt_pct": -2.5, "gold_commodity_pct": 7.5}
    assert result["warnings"] == []
    assert len(result["per_fund"]) == 2


@pytest.mark.asyncio
async def test_get_allocation_gap_reports_warning_on_lookup_failure(monkeypatch):
    fund_id = str(uuid.uuid4())

    async def fake_fetch(fund_id, session):
        raise FundLookupError("fund not found")

    monkeypatch.setattr("app.agent.domains.allocation_gap.fetch_asset_allocation", fake_fetch)

    result = await dispatch(
        "get_allocation_gap",
        {
            "fund_ids": [fund_id],
            "weights": [1.0],
            "target_equity_pct": 50.0,
            "target_debt_pct": 50.0,
            "target_gold_pct": 0.0,
        },
        session=None,
    )

    assert len(result["warnings"]) == 1
    assert "fund not found" in result["warnings"][0]
    assert result["per_fund"] == []


@pytest.mark.asyncio
async def test_get_allocation_gap_mismatched_lengths():
    result = await dispatch(
        "get_allocation_gap",
        {"fund_ids": ["a", "b"], "weights": [1.0], "target_equity_pct": 1, "target_debt_pct": 1, "target_gold_pct": 1},
        session=None,
    )
    assert "error" in result


@pytest.mark.skip(reason="hits the live Groww API; needs a real DB session, run manually")
@pytest.mark.asyncio
async def test_run_live(db_session):
    answer = await allocation_gap.run(
        "I hold Parag Parikh Flexi Cap Fund, and I want 50% equity, 40% debt, 10% gold. What's the gap?",
        db_session,
    )
    assert isinstance(answer, str) and answer

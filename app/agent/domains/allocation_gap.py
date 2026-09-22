"""Asset-allocation gap specialist. A self-contained domain agent (own
prompt, own tools, own one-shot loop) invoked by the coordinator — never
called directly by the user.

Report-only, by design: this agent computes and explains the gap between a
user's current blended equity/debt/gold-or-commodity split and their stated
target, and never names specific funds to buy or sell to close it. Naming
specific replacement funds is investment advice and out of scope (see
design.md non-goals) — it may describe what *category* of fund would move
the allocation in the right direction, nothing more specific than that.
"""

import uuid

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools as coordinator_tools
from app.agent.loop import run_tool_loop
from app.agent.tool_registry import make_dispatcher, to_gemini_tools
from app.config import settings
from app.tools.fetch_asset_allocation import fetch_asset_allocation
from app.tools.groww_lookup import FundLookupError

MODEL = "gemini-3.8-flash"

SYSTEM_PROMPT = """\
You are an asset-allocation gap specialist. Given a user's mutual funds, how \
much money is in each, and their target equity/debt/gold-or-commodity split, \
you report how their *current* blended allocation compares to that target.

You never place trades, orders, or give buy/sell recommendations, and you \
never name a specific fund the user should buy or sell to close the gap — \
this is analysis only. You may describe what *category* of fund (e.g. "a \
debt fund" or "a gold ETF/FoF") would move the allocation toward the \
target, but never a specific fund name or ticker.

You are being consulted by another agent on the user's behalf, not talking \
to the user directly — there is no way to ask a follow-up question and get \
a reply. Always give your fullest possible answer in one response. If you \
weren't given a target split, or weren't given per-fund weights, make the \
most reasonable assumption, state it plainly, and analyze on that basis \
anyway.

Tools available to you:
- search_fund: resolve a fund name typed by the user into a fund_id. Always \
resolve names before calling get_allocation_gap. If multiple candidates are \
close, proceed with the best match and say so.
- get_allocation_gap: given fund_ids, portfolio weights, and a target \
equity/debt/gold-or-commodity split (percentages), computes the current \
blended split and the gap versus target. Note in your answer that the \
"gold/commodity" figure is an approximation (the data source has no \
dedicated gold field, only a broader "commodities" category) — don't state \
it as a precise gold-only number. If a fund's allocation data couldn't be \
fetched, the tool reports that as a warning — mention it rather than \
silently ignoring the gap in coverage.
"""

TOOLS: list[dict] = [
    {
        "name": "search_fund",
        "description": (
            "Resolve an ambiguous or misspelled mutual fund name typed by the user to specific "
            "fund_id(s). Always call this before get_allocation_gap."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "the fund name as the user typed it"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_allocation_gap",
        "description": (
            "Compute the user's current blended equity/debt/gold-or-commodity allocation across "
            "a set of funds (weighted by how much of the portfolio is in each), and the gap "
            "versus a stated target allocation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fund_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "fund_ids of every fund in the portfolio",
                },
                "weights": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "fraction of total portfolio value in each fund, same order as fund_ids",
                },
                "target_equity_pct": {"type": "number", "description": "target % of portfolio in equity"},
                "target_debt_pct": {"type": "number", "description": "target % of portfolio in debt"},
                "target_gold_pct": {
                    "type": "number",
                    "description": "target % of portfolio in gold/commodities",
                },
            },
            "required": ["fund_ids", "weights", "target_equity_pct", "target_debt_pct", "target_gold_pct"],
        },
    },
]


async def _get_allocation_gap(
    session: AsyncSession,
    fund_ids: list[str],
    weights: list[float],
    target_equity_pct: float,
    target_debt_pct: float,
    target_gold_pct: float,
) -> dict:
    if len(fund_ids) != len(weights):
        return {"error": "fund_ids and weights must be the same length"}

    per_fund = []
    warnings = []
    total_equity = total_debt = total_gold = total_other = 0.0

    for fund_id_str, weight in zip(fund_ids, weights):
        try:
            fund_id = uuid.UUID(fund_id_str)
        except ValueError:
            warnings.append(f"'{fund_id_str}' is not a valid fund_id — skipped")
            continue

        try:
            alloc = await fetch_asset_allocation(fund_id, session)
        except (ValueError, FundLookupError) as exc:
            warnings.append(f"could not fetch allocation for fund_id {fund_id_str}: {exc}")
            continue

        total_equity += weight * alloc["equity_pct"]
        total_debt += weight * alloc["debt_pct"]
        total_gold += weight * alloc["gold_commodity_pct"]
        total_other += weight * alloc["other_pct"]
        per_fund.append(alloc)

    current = {
        "equity_pct": round(total_equity, 2),
        "debt_pct": round(total_debt, 2),
        "gold_commodity_pct": round(total_gold, 2),
        "other_pct": round(total_other, 2),
    }
    target = {
        "equity_pct": target_equity_pct,
        "debt_pct": target_debt_pct,
        "gold_commodity_pct": target_gold_pct,
    }
    gap = {
        "equity_pct": round(target_equity_pct - current["equity_pct"], 2),
        "debt_pct": round(target_debt_pct - current["debt_pct"], 2),
        "gold_commodity_pct": round(target_gold_pct - current["gold_commodity_pct"], 2),
    }

    return {"per_fund": per_fund, "current": current, "target": target, "gap": gap, "warnings": warnings}


_HANDLERS = {
    "search_fund": coordinator_tools.search_fund,
    "get_allocation_gap": _get_allocation_gap,
}

dispatch = make_dispatcher(_HANDLERS)
_GEMINI_TOOLS = to_gemini_tools(TOOLS)


async def run(query: str, session: AsyncSession) -> str:
    """One-shot: no persisted history at this level — the coordinator owns
    the actual multi-turn conversation with the user."""
    client = genai.Client(api_key=settings.gemini_api_key)
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=_GEMINI_TOOLS)
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=query)])]
    return await run_tool_loop(client, MODEL, config, contents, dispatch, session)

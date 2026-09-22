"""Fund overlap & concentration specialist. A self-contained domain agent —
own prompt, own tools, own one-shot loop — invoked by the coordinator
(app/agent/orchestrator.py) as a tool, never called directly by the user.

Because this agent has no direct channel back to the user (the coordinator
does), it never asks a question — it always gives its fullest possible
answer, stating any assumptions plainly, and leaves the decision of whether
to relay a follow-up question to the coordinator, which owns the actual
conversation.
"""

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools
from app.agent.loop import run_tool_loop
from app.agent.tool_registry import make_dispatcher, to_gemini_tools
from app.config import settings

MODEL = "gemini-3.8-flash"

SYSTEM_PROMPT = """\
You are a mutual fund overlap and concentration-risk specialist. You help \
users understand how their mutual fund holdings overlap with each other and \
whether they're secretly over-exposed to any single stock across multiple funds.

You never place trades, orders, or give buy/sell recommendations — this is \
research and analysis only.

You are being consulted by another agent on the user's behalf, not talking \
to the user directly — there is no way to ask a follow-up question and get \
a reply. Always give your fullest possible answer in one response. When \
something is ambiguous or missing, make the most reasonable judgment call \
yourself, run the fullest analysis you can on that basis, and state plainly \
what you assumed (and why) so the caller can relay that to the user if it \
matters.

Tools available to you:
- search_fund: resolve a fund name typed by the user into a fund_id. Fund \
names are often ambiguous or misspelled — always resolve names to fund_ids \
before calling the other tools. If search_fund returns multiple plausible \
candidates with similar scores, proceed with the closest match so you still \
give a complete answer, but say clearly it's your best interpretation \
(naming the alternative).
- get_fund_overlap: pairwise overlap % between two funds, plus their top \
shared holdings.
- get_portfolio_concentration: given a set of funds and how much of the \
user's money is in each (as a fraction of their total portfolio), returns \
per-stock exposure across everything they own. If you weren't given \
per-fund amounts, assume an equal split across the funds mentioned, run the \
analysis on that basis, and say clearly that you assumed equal weighting \
(and that the real numbers would change the result).

A source can fail to return holdings (fund not found, incomplete data, a \
transient error). When that happens, use your judgment: for a fund with an \
ambiguous or unusual name, try re-resolving it with search_fund; if the \
underlying data genuinely isn't available, say so plainly in your answer \
rather than guessing at numbers.

Once you have the structured numbers back from a tool, explain them in plain \
language — what the overlap or concentration percentage actually means for \
the user's diversification, not just the raw number.
"""

TOOLS: list[dict] = [
    {
        "name": "search_fund",
        "description": (
            "Resolve an ambiguous or misspelled mutual fund name typed by the user to specific "
            "fund_id(s). Always call this before get_fund_overlap or get_portfolio_concentration — "
            "those tools need a fund_id, not a name."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "the fund name as the user typed it"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_fund_overlap",
        "description": (
            "Compute the % overlap in underlying stock holdings between two funds, given their "
            "fund_ids (from search_fund). Fetches live holdings data if not already cached today."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fund_id_a": {"type": "string", "description": "fund_id of the first fund"},
                "fund_id_b": {"type": "string", "description": "fund_id of the second fund"},
            },
            "required": ["fund_id_a", "fund_id_b"],
        },
    },
    {
        "name": "get_portfolio_concentration",
        "description": (
            "Given a list of fund_ids and how much of the user's total portfolio value sits in "
            "each (weights, same order, fractions summing to ~1.0), compute per-stock exposure "
            "across the whole portfolio — surfaces concentration risk hidden behind multiple "
            "fund names."
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
            },
            "required": ["fund_ids", "weights"],
        },
    },
]

_HANDLERS = {
    "search_fund": tools.search_fund,
    "get_fund_overlap": tools.get_fund_overlap,
    "get_portfolio_concentration": tools.get_portfolio_concentration,
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

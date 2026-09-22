"""Coordinator agent — the only agent the outside world (the CLI) talks to.
Owns the actual multi-turn conversation with the user; routes questions to
domain specialists (currently overlap/concentration and allocation-gap) as
tools, and weaves their answers into one coherent reply.

Adding a new domain later means: write a new self-contained module under
app/agent/domains/ (own prompt, own tools, own `run(query, session) -> str`),
then register one more entry here — not touching the domain that already works.
"""

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.domains import allocation_gap, overlap_concentration
from app.agent.loop import run_tool_loop
from app.agent.tool_registry import make_dispatcher, to_gemini_tools
from app.config import settings

MODEL = "gemini-3.8-flash"

SYSTEM_PROMPT = """\
You are the coordinator for a mutual fund analysis assistant. You never \
answer fund questions yourself — you consult specialist agents (via tools) \
and weave their answers into one coherent reply for the user. You never \
place trades, orders, or give buy/sell recommendations — this is research \
and analysis only.

This is a multi-turn conversation — the user can reply to a question you \
ask, in a follow-up turn. Even so, never withhold an answer just to ask a \
question first: always give the fullest answer you can from what a \
specialist returns, and only add a clarifying question at the end if the \
missing information would meaningfully change the analysis (e.g. a \
specialist had to assume something and getting the real answer would \
change the numbers). Don't ask questions that don't matter.

Tools available to you:
- consult_overlap_agent: for any question about how mutual funds overlap in \
underlying stock holdings, or how concentrated a portfolio is in any single \
stock/sector across multiple funds.
- consult_allocation_gap_agent: for any question comparing a user's current \
equity/debt/gold(or commodity) allocation against a target split they've \
stated, and how large the gap is. Only call this once you know (or the user \
has given you, even roughly) their target percentages — if they haven't \
stated a target, ask for it before calling this tool, since without a \
target there's no gap to report.

For both tools, pass the user's relevant question as `query`, including any \
fund names, portfolio weights, and (for allocation-gap questions) target \
percentages they mentioned — the specialist has no access to the rest of \
the conversation, only what you send it.
"""

TOOLS: list[dict] = [
    {
        "name": "consult_overlap_agent",
        "description": (
            "Consult the fund overlap & concentration specialist. Use for any question about "
            "overlap between funds' stock holdings, or portfolio-wide concentration risk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "the question to hand to the specialist, in natural language, "
                    "including any fund names/weights the user gave",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "consult_allocation_gap_agent",
        "description": (
            "Consult the asset-allocation gap specialist. Use when the user wants to know how "
            "their current equity/debt/gold-or-commodity split compares to a target split they "
            "want (e.g. stated as part of a risk tolerance / investment goal)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "the question to hand to the specialist, in natural language, "
                    "including fund names/weights and the user's target equity/debt/gold split",
                }
            },
            "required": ["query"],
        },
    },
]


async def _consult_overlap_agent(session: AsyncSession, query: str) -> dict:
    answer = await overlap_concentration.run(query, session)
    return {"answer": answer}


async def _consult_allocation_gap_agent(session: AsyncSession, query: str) -> dict:
    answer = await allocation_gap.run(query, session)
    return {"answer": answer}


_HANDLERS = {
    "consult_overlap_agent": _consult_overlap_agent,
    "consult_allocation_gap_agent": _consult_allocation_gap_agent,
}
dispatch = make_dispatcher(_HANDLERS)
_GEMINI_TOOLS = to_gemini_tools(TOOLS)


async def run_agent(
    user_message: str, session: AsyncSession, history: list[dict] | None = None
) -> tuple[str, list[dict]]:
    """The server holds no conversation state — `history` (a plain list of
    {"role": "user"|"model", "text": str} turns, as returned by a prior call)
    is the caller's responsibility to store and resend."""
    history = history or []
    client = genai.Client(api_key=settings.gemini_api_key)
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=_GEMINI_TOOLS)

    contents: list[types.Content] = [
        types.Content(role=turn["role"], parts=[types.Part.from_text(text=turn["text"])]) for turn in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

    reply = await run_tool_loop(client, MODEL, config, contents, dispatch, session)

    updated_history = history + [
        {"role": "user", "text": user_message},
        {"role": "model", "text": reply},
    ]
    return reply, updated_history

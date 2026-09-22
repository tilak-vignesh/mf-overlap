from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools

# Provider-neutral tool definitions: name, description, and a JSON-schema
# `parameters` dict. The orchestrator adapts these into whatever shape the
# model provider's SDK expects (currently Gemini's types.FunctionDeclaration).
# Handlers take (session, **llm_provided_kwargs) and return a JSON-serializable
# dict — `session` is injected per-request, never something the model supplies.

TOOLS: list[dict[str, Any]] = [
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

_HANDLERS: dict[str, Callable[..., Awaitable[dict]]] = {
    "search_fund": tools.search_fund,
    "get_fund_overlap": tools.get_fund_overlap,
    "get_portfolio_concentration": tools.get_portfolio_concentration,
}


async def dispatch(tool_name: str, tool_input: dict, session: AsyncSession) -> dict:
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return {"error": f"unknown tool '{tool_name}'"}
    return await handler(session, **tool_input)

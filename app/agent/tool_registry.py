from collections.abc import Awaitable, Callable
from typing import Any

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

# Generic helpers shared by every agent (coordinator or domain specialist).
# Each agent defines its own provider-neutral TOOLS (`{name, description,
# parameters}`, plain JSON schema) and its own name -> handler map; these two
# functions turn that into what the loop actually needs.

Handler = Callable[..., Awaitable[dict]]


def to_gemini_tools(tools: list[dict[str, Any]]) -> list[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in tools
            ]
        )
    ]


def make_dispatcher(handlers: dict[str, Handler]) -> Callable[[str, dict, AsyncSession], Awaitable[dict]]:
    async def dispatch(tool_name: str, tool_input: dict, session: AsyncSession) -> dict:
        handler = handlers.get(tool_name)
        if handler is None:
            return {"error": f"unknown tool '{tool_name}'"}
        return await handler(session, **tool_input)

    return dispatch

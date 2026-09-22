from collections.abc import Awaitable, Callable

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

MAX_TOOL_ITERATIONS = 8

Dispatcher = Callable[[str, dict, AsyncSession], Awaitable[dict]]


async def run_tool_loop(
    client: genai.Client,
    model: str,
    config: types.GenerateContentConfig,
    contents: list[types.Content],
    dispatch: Dispatcher,
    session: AsyncSession,
) -> str:
    """Hand-rolled tool-calling loop, shared by every agent (coordinator or
    domain specialist): call the model, dispatch any function_call parts,
    feed results back, repeat until the model produces a final text answer
    or the iteration cap is hit. Mutates `contents` in place."""
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.aio.models.generate_content(model=model, contents=contents, config=config)

        candidate_content = response.candidates[0].content
        function_call_parts = [part for part in candidate_content.parts if part.function_call]

        if not function_call_parts:
            return response.text or "(no response)"

        contents.append(candidate_content)

        response_parts = []
        for part in function_call_parts:
            call = part.function_call
            try:
                result = await dispatch(call.name, dict(call.args), session)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                result = {"error": str(exc)}
            response_parts.append(types.Part.from_function_response(name=call.name, response=result))

        contents.append(types.Content(role="user", parts=response_parts))

    return "I wasn't able to finish this analysis within the allotted number of steps."

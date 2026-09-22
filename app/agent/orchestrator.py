from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tool_registry import TOOLS, dispatch
from app.config import settings

# "Our most intelligent Flash model, engineered for long-horizon software
# engineering, autonomous agents, and complex enterprise workflows" per
# Google's own model docs — matches this agent's shape better than the
# "-pro-preview" tier, which is a preview model.
MODEL = "gemini-3.8-flash"
MAX_TOOL_ITERATIONS = 8

_GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            {"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in TOOLS
        ]
    )
]


async def run_agent(user_message: str, session: AsyncSession) -> str:
    """Hand-rolled tool-calling loop: call Gemini, dispatch any function_call
    parts against the tool registry, feed results back, repeat until Gemini
    produces a final text answer (or we hit the iteration cap)."""
    client = genai.Client(api_key=settings.gemini_api_key)
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=_GEMINI_TOOLS)

    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.aio.models.generate_content(model=MODEL, contents=contents, config=config)

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

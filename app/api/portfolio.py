from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import run_agent
from app.db import get_session

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class AnalyzeRequest(BaseModel):
    message: str


class AnalyzeResponse(BaseModel):
    reply: str


@router.post("/analyze")
async def analyze(request: AnalyzeRequest, session: AsyncSession = Depends(get_session)) -> AnalyzeResponse:
    reply = await run_agent(request.message, session)
    return AnalyzeResponse(reply=reply)

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.tools.search_fund import search_fund

router = APIRouter(prefix="/funds", tags=["funds"])


@router.get("/search")
async def search(query: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    matches = await search_fund(query, session)
    await session.commit()
    return [{"fund_id": str(fund_id), "name": name, "score": score} for fund_id, name, score in matches]

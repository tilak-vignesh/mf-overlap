from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fund import Fund
from app.models.stock import Stock


async def get_or_create_fund(session: AsyncSession, *, isin: str, name: str, amc: str) -> Fund:
    result = await session.execute(select(Fund).where(Fund.isin == isin))
    fund = result.scalar_one_or_none()
    if fund is None:
        fund = Fund(isin=isin, name=name, amc=amc)
        session.add(fund)
        await session.flush()
    return fund


async def get_or_create_stock(session: AsyncSession, *, external_id: str, name: str) -> Stock:
    result = await session.execute(select(Stock).where(Stock.external_id == external_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        stock = Stock(external_id=external_id, name=name)
        session.add(stock)
        await session.flush()
    return stock

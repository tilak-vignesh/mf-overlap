import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class HoldingsCache(Base):
    """A fund's disclosed stock holdings, snapshotted by the date the source
    disclosed them (not the date we scraped them). Rows are never deleted —
    old snapshots are kept for free trend history — but only the row matching
    today's `as_of_date` for a fund counts as a cache hit for a fresh fetch."""

    __tablename__ = "holdings_cache"

    fund_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("funds.fund_id"), primary_key=True)
    stock_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stocks.stock_id"), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    weight: Mapped[float] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

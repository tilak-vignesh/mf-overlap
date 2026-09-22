import uuid

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Stock(Base):
    __tablename__ = "stocks"

    stock_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    isin: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # source-specific slug (e.g. Groww's stock_search_id) used to dedupe/look up a
    # stock when a source's holdings payload doesn't carry ISIN (Groww's doesn't)
    external_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)

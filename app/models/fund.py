import uuid

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Fund(Base):
    __tablename__ = "funds"

    fund_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    amc: Mapped[str] = mapped_column(String, nullable=False)
    isin: Mapped[str] = mapped_column(String, unique=True, nullable=False)

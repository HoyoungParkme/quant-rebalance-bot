"""보고 도메인 ORM (QBOT-DOM-003 3.5)."""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class MonthlyReport(IdCreated, Base):
    __tablename__ = "monthly_report"
    __table_args__ = (UniqueConstraint("year_month", "mode", name="uq_report"),)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    factor_effects_json: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

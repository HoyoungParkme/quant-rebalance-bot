"""위험 관리 도메인 ORM (QBOT-DOM-003 3.4). bot_state는 행 하나(id=1)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class BotState(IdCreated, Base):
    __tablename__ = "bot_state"
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    halted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buy_suspended: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peak_equity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peak_reset_at: Mapped[str | None] = mapped_column(String(25))
    principal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    live_since: Mapped[str | None] = mapped_column(String(25))
    gate_record_id: Mapped[int | None] = mapped_column(ForeignKey("gate_record.id"))
    first_month_cap: Mapped[int | None] = mapped_column(Integer)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String(25))
    updated_at: Mapped[str] = mapped_column(String(25), nullable=False)


class Valuation(IdCreated, Base):
    __tablename__ = "valuation"
    date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    cash: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_value: Mapped[int] = mapped_column(Integer, nullable=False)
    index_value: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    external_flow: Mapped[int] = mapped_column(Integer, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_vs_principal: Mapped[float] = mapped_column(Float, nullable=False)


class GateRecord(IdCreated, Base):
    __tablename__ = "gate_record"
    checked_at: Mapped[str] = mapped_column(String(25), nullable=False)
    paper_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rebalances: Mapped[int] = mapped_column(Integer, nullable=False)
    order_errors: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_match: Mapped[float] = mapped_column(Float, nullable=False)
    return_gap: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(4), nullable=False)
    approved_by_command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))
    approved_at: Mapped[str | None] = mapped_column(String(25))
    capital: Mapped[int | None] = mapped_column(Integer)
    first_month_ratio: Mapped[float | None] = mapped_column(Float)

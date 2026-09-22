"""시장 데이터 도메인 ORM (QBOT-DOM-003 3.1). 추가만 하고 고치지 않는다 (INFRA C10)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class Instrument(IdCreated, Base):
    __tablename__ = "instrument"
    code: Mapped[str] = mapped_column(String(6), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # KOSPI, KOSDAQ
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # common, preferred, etf
    listed_on: Mapped[str | None] = mapped_column(String(10))
    delisted_on: Mapped[str | None] = mapped_column(String(10))
    prev_code: Mapped[str | None] = mapped_column(String(6))
    shares_outstanding: Mapped[int | None] = mapped_column(Integer)
    shares_asof: Mapped[str | None] = mapped_column(String(10))


class InstrumentStatus(IdCreated, Base):
    __tablename__ = "instrument_status"
    __table_args__ = (Index("ix_status_inst_range", "instrument_id", "starts_on", "ends_on"),)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    starts_on: Mapped[str] = mapped_column(String(10), nullable=False)
    ends_on: Mapped[str | None] = mapped_column(String(10))


class DailyBar(IdCreated, Base):
    __tablename__ = "daily_bar"
    __table_args__ = (
        UniqueConstraint("instrument_id", "series_no", "trade_date", name="uq_bar"),
        Index("ix_bar_date", "trade_date"),
    )
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    series_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    open: Mapped[int] = mapped_column(Integer, nullable=False)
    high: Mapped[int] = mapped_column(Integer, nullable=False)
    low: Mapped[int] = mapped_column(Integer, nullable=False)
    close: Mapped[int] = mapped_column(Integer, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int | None] = mapped_column(Integer)
    traded: Mapped[int] = mapped_column(Integer, nullable=False)
    collected_at: Mapped[str] = mapped_column(String(25), nullable=False)


class Filing(IdCreated, Base):
    __tablename__ = "filing"
    __table_args__ = (Index("ix_filing_inst_rcept", "instrument_id", "rcept_date"),)
    rcept_no: Mapped[str] = mapped_column(String(14), unique=True, nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    report_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    rcept_date: Mapped[str] = mapped_column(String(10), nullable=False)
    corrects_rcept_no: Mapped[str | None] = mapped_column(String(14))
    title: Mapped[str] = mapped_column(String, nullable=False)
    collected_at: Mapped[str] = mapped_column(String(25), nullable=False)


class FinancialSnapshot(IdCreated, Base):
    __tablename__ = "financial_snapshot"
    __table_args__ = (
        UniqueConstraint("filing_id", "period_end", "period_kind", "consolidated", name="uq_snapshot"),
        Index("ix_snapshot_inst_period", "instrument_id", "period_end"),
    )
    filing_id: Mapped[int] = mapped_column(ForeignKey("filing.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(8), nullable=False)  # annual, quarter, prelim
    consolidated: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue: Mapped[int | None] = mapped_column(Integer)
    operating_income: Mapped[int | None] = mapped_column(Integer)
    net_income: Mapped[int | None] = mapped_column(Integer)
    net_income_owner: Mapped[int | None] = mapped_column(Integer)
    equity: Mapped[int | None] = mapped_column(Integer)
    equity_owner: Mapped[int | None] = mapped_column(Integer)
    eps: Mapped[int | None] = mapped_column(Integer)


class TradingCalendar(IdCreated, Base):
    __tablename__ = "trading_calendar"
    date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    is_trading_day: Mapped[int] = mapped_column(Integer, nullable=False)
    open_at: Mapped[str | None] = mapped_column(String(5))
    close_at: Mapped[str | None] = mapped_column(String(5))
    is_month_end: Mapped[int] = mapped_column(Integer, nullable=False)


class IndexLevel(IdCreated, Base):
    __tablename__ = "index_level"
    __table_args__ = (UniqueConstraint("index_name", "date", name="uq_index"),)
    index_name: Mapped[str] = mapped_column(String(10), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)

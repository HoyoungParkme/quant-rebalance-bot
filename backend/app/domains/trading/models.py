"""매매 도메인 ORM (QBOT-DOM-003 3.3). order는 예약어라 trade_order."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class TradeOrder(IdCreated, Base):
    __tablename__ = "trade_order"
    __table_args__ = (Index("ix_order_status", "status"), Index("ix_order_decision", "decision_id"))
    idem_key: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)  # 멱등성 (UC-S3)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(6), nullable=False)
    limit_price: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(9), nullable=False)
    broker_order_no: Mapped[str | None] = mapped_column(String(20))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reject_reason: Mapped[str | None] = mapped_column(String(40))
    sent_at: Mapped[str | None] = mapped_column(String(25))
    closed_at: Mapped[str | None] = mapped_column(String(25))


class Fill(IdCreated, Base):
    __tablename__ = "fill"
    __table_args__ = (UniqueConstraint("order_id", "broker_fill_no", name="uq_fill"),)
    order_id: Mapped[int] = mapped_column(ForeignKey("trade_order.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    fee: Mapped[int] = mapped_column(Integer, nullable=False)
    tax: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_at: Mapped[str] = mapped_column(String(25), nullable=False)
    broker_fill_no: Mapped[str] = mapped_column(String(40), nullable=False)  # 없으면 실행기가 합성 키를 넣는다


class Position(IdCreated, Base):
    __tablename__ = "position"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), unique=True, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    first_bought_on: Mapped[str | None] = mapped_column(String(10))
    updated_at: Mapped[str] = mapped_column(String(25), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(14), nullable=False)


class Reconciliation(IdCreated, Base):
    __tablename__ = "reconciliation"
    checked_at: Mapped[str] = mapped_column(String(25), nullable=False)
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    bot_cash: Mapped[int] = mapped_column(Integer, nullable=False)
    broker_cash: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    # 이 시점까지 반영한 체결. 다음 대조는 이 뒤의 체결만 더해 예수금을 설명한다.
    # 시각으로 자르면 같은 초에 일어난 대조와 체결의 앞뒤를 가릴 수 없다
    last_fill_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 사람이 "이만큼은 입출금"이라고 밝힌 금액. 원금·고점 기준선은 이것만 따라 움직인다
    external_flow: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolution: Mapped[str | None] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(String(200))
    resolved_by_command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))
    resolved_at: Mapped[str | None] = mapped_column(String(25))


class ReconciliationDiff(IdCreated, Base):
    __tablename__ = "reconciliation_diff"
    reconciliation_id: Mapped[int] = mapped_column(ForeignKey("reconciliation.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    bot_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    broker_qty: Mapped[int] = mapped_column(Integer, nullable=False)

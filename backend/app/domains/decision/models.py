"""판단 도메인 ORM (QBOT-DOM-003 3.2)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class StrategyConfig(IdCreated, Base):
    __tablename__ = "strategy_config"
    factors_json: Mapped[str] = mapped_column(Text, nullable=False)
    min_price: Mapped[int] = mapped_column(Integer, nullable=False)
    min_amount20: Mapped[int] = mapped_column(Integer, nullable=False)
    min_mcap: Mapped[int] = mapped_column(Integer, nullable=False)
    n_holdings: Mapped[int] = mapped_column(Integer, nullable=False)
    trend_filter: Mapped[int] = mapped_column(Integer, nullable=False)
    index_weight: Mapped[float] = mapped_column(Float, nullable=False)
    effective_from: Mapped[str] = mapped_column(String(10), nullable=False)
    approved_by_command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))
    source_review_id: Mapped[int | None] = mapped_column(ForeignKey("rule_review.id"))


class Decision(IdCreated, Base):
    __tablename__ = "decision"
    __table_args__ = (
        # 같은 날의 실제 판단은 하나. 재현(replay)은 여럿 가능 (QBOT-DOM-003 4장)
        Index("uq_decision_real", "asof", "mode", unique=True, sqlite_where=text("status != 'replay'")),
        Index("ix_decision_status", "status"),
    )
    asof: Mapped[str] = mapped_column(String(10), nullable=False)
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    strategy_config_id: Mapped[int] = mapped_column(ForeignKey("strategy_config.id"), nullable=False)
    code_version: Mapped[str] = mapped_column(String(40), nullable=False)
    cash_switch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_rebalance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # pending running partial done replay
    budget_per_slot: Mapped[int | None] = mapped_column(Integer)  # 판단 시점 추정치. 실행 시 재계산


class Score(IdCreated, Base):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("decision_id", "instrument_id", name="uq_score"),)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"), nullable=False)
    factors_json: Mapped[str] = mapped_column(Text, nullable=False)
    pct_json: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[int] = mapped_column(Integer, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(8))


class RuleReview(IdCreated, Base):
    __tablename__ = "rule_review"
    asof: Mapped[str] = mapped_column(String(10), nullable=False)
    results_json: Mapped[str] = mapped_column(Text, nullable=False)
    adopted_json: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(8))
    decided_by_command_id: Mapped[int | None] = mapped_column(ForeignKey("command.id"))
    decided_at: Mapped[str | None] = mapped_column(String(25))

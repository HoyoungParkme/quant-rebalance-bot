"""판단 도메인 DB 접근. service.py만 부른다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.decision.models import Decision, Score, StrategyConfig
from app.domains.marketdata.models import Instrument


class DecisionCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def config_effective(self, asof: str) -> StrategyConfig | None:
        stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.effective_from <= asof)
            .order_by(StrategyConfig.effective_from.desc(), StrategyConfig.id.desc())
            .limit(1)
        )
        return self.s.scalar(stmt)

    def add_config(self, cfg: StrategyConfig) -> StrategyConfig:
        self.s.add(cfg)
        self.s.flush()
        return cfg

    def real_decision(self, asof: str, mode: str) -> Decision | None:
        stmt = select(Decision).where(Decision.asof == asof, Decision.mode == mode, Decision.status != "replay")
        return self.s.scalar(stmt)

    def add_decision(self, d: Decision, scores: list[Score]) -> Decision:
        self.s.add(d)
        self.s.flush()
        for sc in scores:
            sc.decision_id = d.id
        self.s.add_all(scores)
        self.s.flush()
        return d

    def picks(self, decision_id: int) -> list[str]:
        stmt = (
            select(Instrument.code)
            .join(Score, Score.instrument_id == Instrument.id)
            .where(Score.decision_id == decision_id, Score.selected == 1)
            .order_by(Score.rank)
        )
        return list(self.s.scalars(stmt))

    def ranked_codes(self, decision_id: int) -> list[str]:
        stmt = (
            select(Instrument.code)
            .join(Score, Score.instrument_id == Instrument.id)
            .where(Score.decision_id == decision_id)
            .order_by(Score.rank)
        )
        return list(self.s.scalars(stmt))

    def instrument_ids(self) -> dict[str, int]:
        return {i.code: i.id for i in self.s.scalars(select(Instrument))}

    def pending(self) -> list[Decision]:
        stmt = select(Decision).where(Decision.status.in_(["pending", "running", "partial"])).order_by(Decision.asof)
        return list(self.s.scalars(stmt))

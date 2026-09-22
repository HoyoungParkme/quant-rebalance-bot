"""위험 관리 도메인 DB 접근. bot_state는 행 하나(id=1)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.risk.models import BotState, Valuation


class RiskCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def state_or_none(self) -> BotState | None:
        """만들지 않고 읽기만 한다. 관문은 상태가 없으면 거부해야 하므로 이쪽을 쓴다."""
        return self.s.get(BotState, 1)

    def state(self, mode: str, now_iso: str) -> BotState:
        row = self.s.get(BotState, 1)
        if row is None:
            row = BotState(id=1, mode=mode, updated_at=now_iso)
            self.s.add(row)
            self.s.flush()
        return row

    def valuation_on(self, day: str) -> Valuation | None:
        return self.s.scalars(select(Valuation).where(Valuation.date == day)).first()

    def add_valuation(self, v: Valuation) -> Valuation:
        self.s.add(v)
        self.s.flush()
        return v

    def last_valuation_before(self, day: str) -> Valuation | None:
        return self.s.scalars(select(Valuation).where(Valuation.date < day).order_by(Valuation.date.desc())).first()

    def valuations(self, frm: str, to: str) -> list[Valuation]:
        return list(
            self.s.scalars(
                select(Valuation).where(Valuation.date >= frm, Valuation.date <= to).order_by(Valuation.date)
            )
        )

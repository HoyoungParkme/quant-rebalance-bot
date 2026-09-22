"""위험 관리 도메인 DB 접근. bot_state는 행 하나(id=1)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.risk.models import BotState


class RiskCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def state(self, mode: str, now_iso: str) -> BotState:
        row = self.s.get(BotState, 1)
        if row is None:
            row = BotState(id=1, mode=mode, updated_at=now_iso)
            self.s.add(row)
            self.s.flush()
        return row

"""운영 도메인 DB 접근."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.ops.models import Alert, Command


class OpsCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add_alert(self, a: Alert) -> Alert:
        self.s.add(a)
        self.s.flush()
        return a

    def undelivered(self) -> list[Alert]:
        stmt = select(Alert).where(Alert.delivered == 0).order_by(Alert.id)
        return list(self.s.scalars(stmt))

    def add_command(self, c: Command) -> Command:
        self.s.add(c)
        self.s.flush()
        return c

    def recent_commands(self, n: int = 5) -> list[Command]:
        return list(self.s.scalars(select(Command).order_by(Command.id.desc()).limit(n)))

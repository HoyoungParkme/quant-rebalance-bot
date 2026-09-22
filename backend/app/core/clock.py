"""현재 시각의 유일한 출처. 도메인 코드는 datetime.now()를 직접 부르지 않는다 (QBOT-DOM-002 Clock)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class Clock:
    """운영용. 한국 시간."""

    def now(self) -> datetime:
        return datetime.now(KST)

    def today(self) -> date:
        return self.now().date()


class FrozenClock(Clock):
    """재현·테스트용. 주어진 시각에 고정된다."""

    def __init__(self, at: datetime) -> None:
        self._at = (at if at.tzinfo else at.replace(tzinfo=KST)).astimezone(KST)

    def now(self) -> datetime:
        return self._at

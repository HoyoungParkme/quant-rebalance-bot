"""시점 고정 조회의 기준일 값 객체 (QBOT-PRD-001 R3, QBOT-DOM-002 PointInTime).

시장 데이터 조회 함수는 모두 이 타입을 첫 인자로 받는다. date를 직접 받는 조회 함수는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class PointInTime:
    asof: date

    def bars_until(self) -> date:
        """시세는 기준일 당일 종가까지."""
        return self.asof

    def filings_before(self) -> date:
        """공시는 기준일 전날 접수분까지. 전자공시 API가 접수 시각을 주지 않아 당일분은 쓰지 않는다."""
        return self.asof - timedelta(days=1)

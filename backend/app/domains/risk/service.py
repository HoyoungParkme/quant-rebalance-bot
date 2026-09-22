"""위험 관리 서비스 (QBOT-DOM-002 RiskService). 슬라이스 C2에서는 정지·재개·상태만. 한도·관문은 E·F에서."""

from __future__ import annotations

from collections.abc import Callable

from app.core.clock import Clock
from app.core.errors import Precondition
from app.domains.risk.crud import RiskCrud
from app.domains.risk.models import BotState


class RiskService:
    def __init__(self, session, clock: Clock, mode: str, cancel_open_orders: Callable[[], int] | None = None) -> None:
        self.crud = RiskCrud(session)
        self.clock = clock
        self.mode = mode
        self.cancel_open_orders = cancel_open_orders or (lambda: 0)

    def _now(self) -> str:
        return self.clock.now().isoformat(timespec="seconds")

    def state(self) -> BotState:
        return self.crud.state(self.mode, self._now())

    def state_dict(self) -> dict:
        s = self.state()
        return {
            "halted": bool(s.halted),
            "buy_suspended": bool(s.buy_suspended),
            "orders_blocked": bool(s.orders_blocked),
            "last_heartbeat_at": s.last_heartbeat_at,
            "peak_equity": s.peak_equity,
        }

    def halt(self, reason: str | None) -> tuple[BotState, int]:
        """새 주문을 막고 미체결을 취소한다 (UC-H1). 수집·기록은 계속한다."""
        s = self.state()
        s.halted, s.updated_at = 1, self._now()
        cancelled = self.cancel_open_orders()
        return s, cancelled

    def resume(self, reset_peak: bool, current_equity: int | None = None) -> BotState:
        s = self.state()
        if not s.halted and not s.buy_suspended:
            raise Precondition("정지 상태가 아니다")
        if s.buy_suspended and not reset_peak:
            raise Precondition("손실 한도로 멈춘 상태다. reset_peak가 필요하다 (QBOT-PRD-001 R9)")
        if reset_peak and current_equity is not None:
            s.peak_equity, s.peak_reset_at = current_equity, self._now()
        s.halted, s.buy_suspended, s.updated_at = 0, 0, self._now()
        return s

    def touch_heartbeat(self) -> None:
        s = self.state()
        s.last_heartbeat_at = self._now()

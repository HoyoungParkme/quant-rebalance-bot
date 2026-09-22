"""위험 관리 서비스와 주문 관문 (QBOT-DOM-002 RiskService·OrderGate). 손실 한도·실전 관문은 슬라이스 E·F."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.clock import Clock
from app.core.errors import Precondition
from app.domains.risk.crud import RiskCrud
from app.domains.risk.models import BotState
from app.domains.trading.ports import EquitySnapshot, OrderRequest


@dataclass(frozen=True)
class GateVerdict:
    allowed: bool
    reason: str | None = None


class OrderGate:
    """주문 하나가 나가도 되는지 판정한다 (QBOT-MS-001 OrderGate.check). 판정할 수 없으면 거부다."""

    def __init__(self, session, max_position_weight: float = 0.15, daily_order_cap_multiple: float = 2.0) -> None:
        self.crud = RiskCrud(session)
        self.max_position_weight = max_position_weight
        self.daily_order_cap_multiple = daily_order_cap_multiple

    def check(self, req: OrderRequest, snap: EquitySnapshot) -> GateVerdict:
        s = self.crud.state_or_none()
        if s is None:
            return GateVerdict(False, "no_state")
        if s.halted:
            return GateVerdict(False, "halted")
        if s.orders_blocked:
            return GateVerdict(False, "reconcile")
        if s.mode == "live" and s.gate_record_id is None:
            return GateVerdict(False, "no_gate")
        amount = req.qty * req.price
        if req.side == "buy":
            if s.buy_suspended:
                return GateVerdict(False, "drawdown")
            if snap.total <= 0:
                return GateVerdict(False, "no_equity")
            if (snap.holdings.get(req.code, 0) + amount) / snap.total > self.max_position_weight:
                return GateVerdict(False, "concentration")
        if snap.today_order_amount + amount > snap.total * self.daily_order_cap_multiple:
            return GateVerdict(False, "daily_cap")
        if req.side == "buy" and s.first_month_cap is not None and snap.month_buy_amount + amount > s.first_month_cap:
            return GateVerdict(False, "first_month")
        return GateVerdict(True)


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

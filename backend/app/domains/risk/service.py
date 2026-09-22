"""위험 관리 서비스와 주문 관문 (QBOT-DOM-002 RiskService·OrderGate). 손실 한도·실전 관문은 슬라이스 E·F."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.core.clock import Clock
from app.core.errors import Precondition
from app.domains.risk.crud import RiskCrud
from app.domains.risk.models import BotState, Valuation
from app.domains.trading.ports import EquitySnapshot, OrderRequest

GATE_REASONS = frozenset(
    {"no_state", "halted", "reconcile", "no_gate", "drawdown", "no_equity", "concentration", "daily_cap", "first_month"}
)


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

    def block_orders(self, reason: str) -> BotState:
        """계좌 불일치 등으로 새 주문을 막는다. 사람이 정리해야 풀린다 (UC-S4)."""
        s = self.state()
        s.orders_blocked, s.updated_at = 1, self._now()
        return s

    def unblock_orders(self) -> BotState:
        s = self.state()
        s.orders_blocked, s.updated_at = 0, self._now()
        return s

    def touch_heartbeat(self) -> None:
        s = self.state()
        s.last_heartbeat_at = self._now()


class ValuationRecorder:
    """일별 평가액과 손실 한도 (QBOT-MS-001 RiskService.record_valuation).

    입출금을 빼고 고점을 갱신한다. 돈을 넣었다고 고점이 오르면, 그 뒤 원래 자리로 돌아온 것이
    손실로 잡혀 매수가 멈춘다. 반대로 출금하면 손실 한도에 걸리기 쉬워진다.
    """

    def __init__(
        self,
        risk: RiskService,
        balance_fn,
        flow_fn=None,
        notify=None,
        max_drawdown: float = 0.30,
        index_etf: str = "069500",
    ) -> None:
        self.risk = risk
        self.balance_fn = balance_fn
        self.flow_fn = flow_fn or (lambda day: 0)
        self.notify = notify
        self.max_drawdown = max_drawdown
        self.index_etf = index_etf

    def record(self, d: date) -> Valuation:
        bal = self.balance_fn()
        day = d.isoformat()
        flow = self.flow_fn(day)  # 사람이 "입금이다"라고 받아들인 금액만 외부 유입으로 센다
        s = self.risk.state()
        total = bal.total_equity

        row = self.risk.crud.valuation_on(day)
        # 같은 날 두 번 돌려도(재시작·수동 재실행) 입출금이 두 번 더해지면 안 된다. 이미 반영한 만큼을 뺀다
        new_flow = flow - (row.external_flow if row else 0)
        s.principal = (s.principal or 0) + new_flow
        if s.principal <= 0:
            s.principal = total  # 첫 기록. 지금 있는 돈이 원금이다
        # 입출금만큼 고점 기준선을 같이 옮긴다. MS-001 4단계는 "고점과 (평가액-입금)을 비교"라고 적었지만
        # 그러면 1,000만원을 넣은 뒤에는 폭락해도 고점 대비 플러스라 손실 한도가 영영 안 걸린다
        s.peak_equity = (s.peak_equity or 0) + new_flow
        if total > s.peak_equity or s.peak_equity <= 0:
            s.peak_equity = total
        drawdown = (total / s.peak_equity - 1) if s.peak_equity else 0.0
        pnl = (total / s.principal - 1) if s.principal else 0.0

        idx = bal.holdings.get(self.index_etf)
        index_value = idx.value if idx else 0
        fields = {
            "mode": self.risk.mode,
            "cash": bal.cash,
            "index_value": index_value,
            "stock_value": max(total - bal.cash - index_value, 0),
            "total": total,
            "external_flow": flow,
            "drawdown": drawdown,
            "pnl_vs_principal": pnl,
        }
        if row is None:
            row = self.risk.crud.add_valuation(Valuation(date=day, **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        s.updated_at = self.risk._now()

        if drawdown <= -self.max_drawdown and not s.buy_suspended:
            s.buy_suspended = 1
            if self.notify:
                self.notify(
                    "error",
                    f"고점 대비 {drawdown:.1%} (한도 -{self.max_drawdown:.0%}). 새 매수를 멈춘다. "
                    f"평가액 {total:,}원, 고점 {s.peak_equity:,}원. 계속하려면 resume reset_peak",
                )
        return row

"""가짜 증권사(주문). 체결 계획을 주문 순서대로 소비한다."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.errors import BrokerRejected, BrokerUnavailable
from app.domains.trading.ports import Balance, BrokerOrder, Holding, OrderRequest

PRICE = 50_000


@dataclass
class FakeOrderBroker:
    fill_plan: list[str] = field(default_factory=list)  # 주문마다 full · half · none · reject · boom
    default_fill: str = "full"
    cash: int = 1_000_000
    total: int = 1_000_000
    holdings: dict[str, Holding] = field(default_factory=dict)
    orders: list[BrokerOrder] = field(default_factory=list)
    placed: list[OrderRequest] = field(default_factory=list)
    extra_today: list[BrokerOrder] = field(default_factory=list)
    cancel_works: bool = True
    cancelled: list[str] = field(default_factory=list)
    seq: int = 0

    def _mode(self) -> str:
        return self.fill_plan.pop(0) if self.fill_plan else self.default_fill

    def place_order(self, req: OrderRequest) -> str:
        self.placed.append(req)
        mode = self._mode()
        if mode == "reject":
            raise BrokerRejected("40240000 주문가능금액이 부족합니다")
        if mode == "boom":
            raise TimeoutError("응답 없음")
        if mode == "busy":
            raise BrokerUnavailable("EGW00201 초당 거래건수를 초과하였습니다")
        self.seq += 1
        no = str(1000 + self.seq)
        filled = {"full": req.qty, "half": req.qty // 2, "none": 0}[mode]
        self.orders.append(BrokerOrder(no, req.code, req.side, req.qty, filled, PRICE if filled else 0, raw_no=no))
        return no

    def place_order_for_test(self, code: str, side: str, qty: int, filled: int = 0, avg: int = PRICE) -> str:
        """테스트가 증권사 쪽에만 주문을 만들어 둘 때 쓴다."""
        self.seq += 1
        no = str(1000 + self.seq)
        self.orders.append(BrokerOrder(no, code, side, qty, filled, avg if filled else 0, raw_no=no))
        return no

    def cancel_order(self, order_no: str) -> bool:
        if not self.cancel_works:
            return False
        for i, o in enumerate(self.orders):
            if o.order_no == order_no and not o.cancelled and o.filled_qty < o.qty:
                self.orders[i] = replace(o, cancelled=True)
                self.cancelled.append(order_no)
                return True
        return False

    def order_status(self, order_no: str) -> BrokerOrder | None:
        return next((o for o in self.orders if o.order_no == order_no), None)

    def orders_today(self) -> list[BrokerOrder]:
        return [*self.orders, *self.extra_today]

    def balance(self) -> Balance:
        return Balance(self.cash, self.total, self.holdings)


@dataclass
class AllowGate:
    reason: str | None = None

    def check(self, req, snap):
        from app.domains.risk.service import GateVerdict

        return GateVerdict(self.reason is None, self.reason)

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
    fixed_total: bool = False  # True면 total을 그대로 쓴다(관문 시험용)
    cancelled: list[str] = field(default_factory=list)
    seq: int = 0

    def _mode(self) -> str:
        return self.fill_plan.pop(0) if self.fill_plan else self.default_fill

    def hold(self, code: str, qty: int, price: int = PRICE) -> None:
        self.holdings[code] = Holding(code, qty, price, qty * price)

    def _settle(self, req: OrderRequest, filled: int) -> None:
        """체결된 만큼 계좌를 움직인다. 매도 대금이 현금으로 들어와야 매수 예산 계산을 시험할 수 있다."""
        if filled <= 0:
            return
        h = self.holdings.get(req.code)
        have = h.qty if h else 0
        if req.side == "buy":
            self.cash -= filled * PRICE
            self.hold(req.code, have + filled)
        else:
            self.cash += filled * PRICE
            left = max(have - filled, 0)
            if left:
                self.hold(req.code, left)
            else:
                self.holdings.pop(req.code, None)

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
        self._settle(req, filled)
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
        total = self.cash + sum(h.value for h in self.holdings.values())
        return Balance(self.cash, self.total if self.fixed_total else total, dict(self.holdings))


@dataclass
class AllowGate:
    reason: str | None = None

    def check(self, req, snap):
        from app.domains.risk.service import GateVerdict

        return GateVerdict(self.reason is None, self.reason)

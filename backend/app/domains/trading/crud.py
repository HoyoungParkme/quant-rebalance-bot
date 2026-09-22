"""매매 도메인 DB 접근. service.py만 부른다."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domains.trading.models import Fill, TradeOrder

OPEN_STATUSES = ("planned", "unknown", "sent")


class TradingCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def order_by_key(self, idem_key: str) -> TradeOrder | None:
        return self.s.scalars(select(TradeOrder).where(TradeOrder.idem_key == idem_key)).first()

    def orders_by_status(self, *statuses: str) -> list[TradeOrder]:
        return list(self.s.scalars(select(TradeOrder).where(TradeOrder.status.in_(statuses)).order_by(TradeOrder.id)))

    def broker_nos_in_use(self, sent_on: str) -> set[str]:
        """그날 이미 쓰고 있는 증권사 주문번호. 미확정 매칭에서 겹치지 않게 한다.

        주문번호는 날마다 새로 매겨져 어제 번호가 오늘 다시 나올 수 있다. 날짜로 좁히지 않으면
        멀쩡한 짝을 "이미 쓴 번호"로 오해해 전송된 주문을 planned로 되돌리고, 같은 주문이 두 번 나간다.
        """
        rows = self.s.scalars(
            select(TradeOrder.broker_order_no).where(
                TradeOrder.broker_order_no.is_not(None), TradeOrder.sent_at.like(f"{sent_on}%")
            )
        )
        return {r for r in rows if r}

    def add_order(self, o: TradeOrder) -> TradeOrder:
        self.s.add(o)
        self.s.flush()
        return o

    def fills_of(self, order_id: int) -> list[Fill]:
        return list(self.s.scalars(select(Fill).where(Fill.order_id == order_id).order_by(Fill.id)))

    def filled_qty(self, order_id: int) -> int:
        return int(self.s.scalar(select(func.coalesce(func.sum(Fill.qty), 0)).where(Fill.order_id == order_id)) or 0)

    def add_fill(self, f: Fill) -> Fill:
        self.s.add(f)
        self.s.flush()
        return f

    def filled_amount_between(self, frm: str, to: str, side: str | None = None) -> int:
        """기간 안 체결 금액 합(수수료·세금 제외). 관문의 하루 한도·첫 달 상한이 쓴다."""
        stmt = (
            select(func.coalesce(func.sum(Fill.qty * Fill.price), 0))
            .join(TradeOrder, Fill.order_id == TradeOrder.id)
            .where(Fill.filled_at >= frm, Fill.filled_at <= to + "T23:59:59")
        )
        if side:
            stmt = stmt.where(TradeOrder.side == side)
        return int(self.s.scalar(stmt) or 0)

    def open_orders(self) -> list[TradeOrder]:
        """아직 끝나지 않은 주문. 정지(halt)가 취소하고 재시작이 확인한다."""
        return list(
            self.s.scalars(
                select(TradeOrder)
                .where(or_(TradeOrder.status.in_(OPEN_STATUSES), TradeOrder.status == "partial"))
                .where(TradeOrder.closed_at.is_(None))
                .order_by(TradeOrder.id)
            )
        )

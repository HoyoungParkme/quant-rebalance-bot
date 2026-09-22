"""매매 도메인 DB 접근. service.py만 부른다."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domains.trading.models import Fill, Position, Reconciliation, ReconciliationDiff, TradeOrder

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

    # ----- 보유 -----
    def positions(self) -> list[Position]:
        return list(self.s.scalars(select(Position).where(Position.qty > 0).order_by(Position.instrument_id)))

    def position(self, instrument_id: int) -> Position | None:
        return self.s.scalars(select(Position).where(Position.instrument_id == instrument_id)).first()

    def upsert_position(self, instrument_id: int, qty: int, avg_cost: int, now_iso: str, by: str, first_on=None):
        p = self.position(instrument_id)
        if p is None:
            p = Position(
                instrument_id=instrument_id,
                qty=qty,
                avg_cost=avg_cost,
                first_bought_on=first_on,
                updated_at=now_iso,
                updated_by=by,
            )
            self.s.add(p)
            self.s.flush()
            return p
        p.qty, p.avg_cost, p.updated_at, p.updated_by = qty, avg_cost, now_iso, by
        if first_on and not p.first_bought_on:
            p.first_bought_on = first_on
        return p

    # ----- 대조 -----
    def add_reconciliation(self, r: Reconciliation, diffs: list[ReconciliationDiff]) -> Reconciliation:
        self.s.add(r)
        self.s.flush()
        for d in diffs:
            d.reconciliation_id = r.id
            self.s.add(d)
        self.s.flush()
        return r

    def last_mismatch(self) -> Reconciliation | None:
        return self.s.scalars(
            select(Reconciliation)
            .where(Reconciliation.result == "mismatch", Reconciliation.resolution.is_(None))
            .order_by(Reconciliation.id.desc())
        ).first()

    def diffs_of(self, reconciliation_id: int) -> list[ReconciliationDiff]:
        return list(
            self.s.scalars(select(ReconciliationDiff).where(ReconciliationDiff.reconciliation_id == reconciliation_id))
        )

    def last_settled(self) -> Reconciliation | None:
        """마지막으로 계좌와 맞았다고 본 대조. 불일치 행은 기준이 될 수 없다.

        불일치 행의 broker_cash를 기준으로 삼으면 다음 대조가 "지금 계좌"에 다시 닻을 내려
        설명 안 되던 차이가 저절로 사라진다(사람이 아무것도 안 했는데 ok).
        """
        return self.s.scalars(
            select(Reconciliation)
            .where((Reconciliation.result == "ok") | (Reconciliation.resolution == "accepted"))
            .order_by(Reconciliation.id.desc())
        ).first()

    def max_fill_id(self) -> int:
        return int(self.s.scalar(select(func.coalesce(func.max(Fill.id), 0))) or 0)

    def cash_flow_after(self, fill_id: int) -> int:
        """그 체결 뒤 우리 체결이 만든 현금 변동. 매도는 들어오고(수수료·세금 뺀), 매수는 나간다.

        계좌 현금을 우리 기록으로 설명할 수 있어야 한다. 설명되지 않는 차이가 곧 "누가 딴 데서 만졌다"이다.
        """
        rows = self.s.execute(
            select(TradeOrder.side, Fill.qty, Fill.price, Fill.fee, Fill.tax)
            .join(TradeOrder, Fill.order_id == TradeOrder.id)
            .where(Fill.id > fill_id)
        )
        total = 0
        for side, qty, price, fee, tax in rows:
            amount = qty * price
            total += (amount - fee - tax) if side == "sell" else -(amount + fee)
        return total

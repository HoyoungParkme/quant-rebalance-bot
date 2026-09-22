"""매매 서비스와 주문 실행기 (QBOT-MS-001 OrderExecutor.send·reconcile_unknown).

주문 하나의 상태 흐름: planned → unknown → sent → filled/partial/cancelled.
`unknown`은 "증권사에 갔는지 모른다"는 뜻이고, 이 값으로 커밋한 **뒤에** 전송한다.
그래야 전송 도중 죽어도 흔적이 남고, 재시작 때 reconcile_unknown이 정리한다 (UC-S3).

`rejected`는 "확실히 체결되지 않았다"이므로 다시 부르면 다시 판정한다. 정지·대조 중처럼
그때의 상태 때문에 거부된 주문이 영영 막히면, 상태를 풀어도 그 달 리밸런싱이 조용히 비어 버린다.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BrokerRejected, BrokerSendFailed, BrokerUnavailable
from app.domains.trading.crud import TradingCrud
from app.domains.trading.models import Fill, TradeOrder
from app.domains.trading.ports import Balance, BrokerOrder, EquitySnapshot, OrderBrokerPort, OrderRequest

CLOSED_STATUSES = ("filled", "partial", "cancelled")  # 여기 오면 끝. rejected는 다시 판정한다
FEE_RATE = 0.00014  # 한투 온라인 위탁수수료. 증권사 응답에 없어 추정한다
TAX_RATE = 0.0018  # 매도 시 증권거래세 + 농어촌특별세 (2025년)


class OrderExecutor:
    def __init__(
        self,
        session: Session,
        broker: OrderBrokerPort,
        gate,
        clock: Clock,
        snapshot_fn: Callable[[], EquitySnapshot],
        code_of: Callable[[int], str | None],
        max_retries: int = 1,
        poll_interval: float = 3.0,
        max_polls: int = 20,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.s = session
        self.crud = TradingCrud(session)
        self.broker = broker
        self.gate = gate
        self.clock = clock
        self.snapshot_fn = snapshot_fn
        self.code_of = code_of
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.sleep = sleep

    def _now(self) -> str:
        return self.clock.now().isoformat(timespec="seconds")

    # ----- 주문 하나 -----
    def send(
        self,
        decision_id: int,
        instrument_id: int,
        code: str,
        side: str,
        qty: int,
        price: int,
        order_type: str = "market",
    ) -> TradeOrder:
        """같은 (판단, 종목, 방향)으로 두 번 불러도 증권사 호출은 한 번이다."""
        key = f"{decision_id}:{code}:{side}"
        o = self.crud.order_by_key(key)
        if o is not None:
            if o.status in CLOSED_STATUSES:
                return o
            if o.status in ("unknown", "sent"):
                return self._confirm(o, price)  # 전송은 건너뛰고 체결 확인부터
            # planned(아직 안 보냄) · rejected(확실히 미체결)는 아래에서 다시 판정한다

        req = OrderRequest(code=code, side=side, qty=qty, price=price, order_type=order_type)
        verdict = self.gate.check(req, self.snapshot_fn())
        args = (key, decision_id, instrument_id, side, qty, order_type, price)
        if not verdict.allowed:
            return self._reject(o, args, verdict.reason)

        o = self._open_row(o, args)
        if o.status in CLOSED_STATUSES:  # 경쟁 상태: 다른 경로가 이미 끝냈다
            return o

        try:
            no = self.broker.place_order(req)
        except BrokerRejected as e:
            o.status, o.reject_reason, o.closed_at = "rejected", str(e)[:40], self._now()
            self.s.commit()
            return o
        except BrokerUnavailable:
            o.status = "planned"  # 증권사가 받지 않았다(초당 한도 등). 확실히 미체결이라 되돌린다
            self.s.commit()
            raise
        except Exception as e:
            self.s.commit()  # unknown 유지. 재시작 때 reconcile_unknown이 확인한다
            raise BrokerSendFailed(f"{code} {side} {qty}주 전송 실패: {e}") from e

        o.broker_order_no, o.status, o.sent_at = no, "sent", self._now()
        self.s.commit()
        return self._confirm(o, price)

    def _reject(self, o: TradeOrder | None, args: tuple, reason: str | None) -> TradeOrder:
        if o is None:
            return self._insert(*args, "rejected", reason)
        o.status, o.reject_reason, o.closed_at = "rejected", reason, self._now()
        self.s.commit()
        return o

    def _open_row(self, o: TradeOrder | None, args: tuple) -> TradeOrder:
        """전송 직전 상태(unknown)로 만든다. 거부로 닫혔던 행도 여기서 되살아난다."""
        qty, order_type, price = args[4], args[5], args[6]
        if o is None:
            o = self._insert(*args, "planned")
            if o.status in CLOSED_STATUSES:
                return o
        o.qty, o.order_type = qty, order_type
        o.limit_price = price if order_type == "limit" else None
        o.status, o.reject_reason, o.closed_at = "unknown", None, None
        self.s.commit()  # 전송 전에 흔적을 남긴다
        return o

    def _insert(
        self,
        key: str,
        decision_id: int,
        instrument_id: int,
        side: str,
        qty: int,
        order_type: str,
        price: int,
        status: str,
        reject_reason: str | None = None,
    ) -> TradeOrder:
        row = TradeOrder(
            idem_key=key,
            decision_id=decision_id,
            instrument_id=instrument_id,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=price if order_type == "limit" else None,
            status=status,
            reject_reason=reject_reason,
            closed_at=self._now() if status == "rejected" else None,
        )
        try:
            self.crud.add_order(row)
            self.s.commit()
            return row
        except IntegrityError:  # 같은 키가 이미 있다. 그 행을 쓴다
            self.s.rollback()
            existing = self.crud.order_by_key(key)
            if existing is None:
                raise
            return existing

    # ----- 체결 확인 -----
    def _confirm(self, o: TradeOrder, price: int) -> TradeOrder:
        while True:
            st = self._poll(o)
            filled = self.crud.filled_qty(o.id)
            if filled >= o.qty:
                return self._close(o, "filled")
            if st is None:
                return o  # 증권사 내역에 없다. 전송됐는지 알 수 없으니 상태를 바꾸지 않는다
            if not st.cancelled and not self._cancel(o):
                return o  # 취소가 확인되지 않았다. 살아 있는 주문 위에 또 내면 두 번 산다
            filled = self.crud.filled_qty(o.id)
            if filled >= o.qty:
                return self._close(o, "filled")
            if o.retry_count >= self.max_retries:
                return self._close(o, "partial" if filled else "cancelled")

            o.retry_count, o.status = o.retry_count + 1, "unknown"
            self.s.commit()  # 재시도도 전송 전에 흔적을 남긴다
            remaining = o.qty - filled
            # 남은 수량을 시장가로 다시 낸다 (MS-001 미결 "재시도 가격 폭"은 v1에서 시장가로 정한다)
            req = OrderRequest(code=self.code_of(o.instrument_id) or "", side=o.side, qty=remaining, price=price)
            try:
                no = self.broker.place_order(req)
            except (BrokerRejected, BrokerUnavailable) as e:  # 확실히 미체결. 남은 수량은 포기한다
                o.reject_reason = str(e)[:40]
                return self._close(o, "partial" if filled else "cancelled")
            except Exception as e:
                self.s.commit()  # unknown 유지. 남은 수량으로 맞춰 확정한다
                raise BrokerSendFailed(f"{o.idem_key} 재주문 전송 실패: {e}") from e
            o.broker_order_no, o.status, o.sent_at = no, "sent", self._now()
            self.s.commit()

    def _poll(self, o: TradeOrder) -> BrokerOrder | None:
        """제한 시간까지 체결을 확인한다. 마지막으로 본 증권사 주문 상태를 돌려준다."""
        if not o.broker_order_no:
            return None  # 볼 것이 없다. 기다려도 달라지지 않는다
        st = None
        for i in range(self.max_polls):
            st = self.broker.order_status(o.broker_order_no)
            if st is not None:
                self._record_fills(o, st)
                if self.crud.filled_qty(o.id) >= o.qty or st.cancelled or st.filled_qty >= st.qty:
                    return st
            if i < self.max_polls - 1:
                self.sleep(self.poll_interval)
        return st

    def _cancel(self, o: TradeOrder) -> bool:
        """취소를 시도하고 증권사 상태로 확인한다. 확실히 닫혔을 때만 True."""
        try:
            self.broker.cancel_order(o.broker_order_no or "")
        except Exception:  # noqa: BLE001 - 취소 실패는 상태 재확인으로 판정한다
            pass
        st = self.broker.order_status(o.broker_order_no or "")
        if st is None:
            return False
        self._record_fills(o, st)
        return st.cancelled or st.filled_qty >= st.qty

    def _record_fills(self, o: TradeOrder, st: BrokerOrder) -> None:
        """증권사는 누적 체결과 누적 평균가만 준다. 이번에 늘어난 수량과 그 구간 값만 넣는다."""
        if st.filled_qty <= 0:
            return
        prefix = st.order_no + ":"
        mine = [f for f in self.crud.fills_of(o.id) if f.broker_fill_no.startswith(prefix)]
        delta = st.filled_qty - sum(f.qty for f in mine)
        if delta <= 0:
            return
        # 평균가는 누적이라 그대로 쓰면 금액이 어긋난다. 누적 금액의 차이를 늘어난 수량으로 나눈다
        amount = max(st.filled_qty * st.avg_price - sum(f.qty * f.price for f in mine), 0)
        self.crud.add_fill(
            Fill(
                order_id=o.id,
                qty=delta,
                price=round(amount / delta),
                fee=int(amount * FEE_RATE),
                tax=int(amount * TAX_RATE) if o.side == "sell" else 0,
                filled_at=self._now(),
                broker_fill_no=f"{prefix}{st.filled_qty}",  # 누적 수량이 키. 같은 값이면 같은 체결이다
            )
        )
        self.s.commit()

    def _close(self, o: TradeOrder, status: str) -> TradeOrder:
        o.status, o.closed_at = status, self._now()
        self.s.commit()
        return o

    # ----- 재시작 정리 -----
    def reconcile_unknown(self) -> list[TradeOrder]:
        """`unknown`인 주문을 증권사 오늘 내역과 맞춰 확정한다 (UC-A4)."""
        unknowns = self.crud.orders_by_status("unknown")
        if not unknowns:
            return []
        today = self.broker.orders_today()
        used = self.crud.broker_nos_in_use(self.clock.today().isoformat())
        out = []
        for o in unknowns:
            code = self.code_of(o.instrument_id)
            # 재시도 중에 죽었으면 증권사에는 "남은 수량"짜리 주문이 있다
            wanted = {o.qty, o.qty - self.crud.filled_qty(o.id)}

            def same(b, code=code, o=o, wanted=wanted, used=used):
                return b.code == code and b.side == o.side and b.qty in wanted and b.order_no not in used

            m = next((b for b in today if same(b)), None)
            if m is not None:
                o.broker_order_no, o.status, o.sent_at = m.order_no, "sent", self._now()
                used.add(m.order_no)
            else:
                o.status = "planned"  # 증권사에 없다 = 전송되지 않았다
            out.append(o)
        self.s.commit()
        for o in [x for x in out if x.status == "sent"]:
            self._confirm(o, o.limit_price or 0)
        return out

    def cancel_open(self) -> int:
        """미체결을 전부 취소한다. 정지(halt)가 부른다. 증권사 오류로 예외를 올리지 않는다."""
        n = 0
        for o in self.crud.open_orders():
            if not o.broker_order_no:
                if o.status == "planned":  # 아직 안 보냈다. 그냥 닫는다
                    self._close(o, "cancelled")
                    n += 1
                continue  # unknown은 보냈는지 모른다. reconcile_unknown이 정리한다
            try:
                closed = self._cancel(o)
            except Exception:  # noqa: BLE001 - 하나가 실패해도 나머지를 계속 취소한다
                continue
            filled = self.crud.filled_qty(o.id)
            if filled >= o.qty:
                self._close(o, "filled")
            elif closed:
                self._close(o, "partial" if filled else "cancelled")
                n += 1
            # 취소가 확인되지 않은 주문은 sent로 둔다. 다음 정리에서 다시 본다
        return n


class TradingService:
    """판단 실행·계좌 대조는 슬라이스 D2. 여기서는 주문 하나와 계좌 읽기만 한다."""

    def __init__(
        self,
        session: Session,
        broker: OrderBrokerPort,
        gate,
        clock: Clock,
        instrument_ids: Callable[[], dict[str, int]],
        **executor_kwargs,
    ) -> None:
        self.s = session
        self.crud = TradingCrud(session)
        self.broker = broker
        self.clock = clock
        self.instrument_ids = instrument_ids
        self._by_id: dict[int, str] | None = None
        self.executor = OrderExecutor(
            session, broker, gate, clock, self.equity_snapshot, self.code_of, **executor_kwargs
        )

    def code_of(self, instrument_id: int) -> str | None:
        """종목 수가 3,500개라 매번 다시 읽지 않는다. 모르는 id일 때만 갱신한다."""
        if self._by_id is None or instrument_id not in self._by_id:
            self._by_id = {i: c for c, i in self.instrument_ids().items()}
        return self._by_id.get(instrument_id)

    def balance(self) -> Balance:
        return self.broker.balance()

    def equity_snapshot(self) -> EquitySnapshot:
        b = self.broker.balance()
        today = self.clock.today().isoformat()
        month_start = today[:8] + "01"
        return EquitySnapshot(
            cash=b.cash,
            total=b.total_equity,
            holdings={c: h.value for c, h in b.holdings.items()},
            today_order_amount=self.crud.filled_amount_between(today, today),
            month_buy_amount=self.crud.filled_amount_between(month_start, today, side="buy"),
        )

    def send(self, decision_id: int, code: str, side: str, qty: int, price: int, order_type: str = "market"):
        iid = self.instrument_ids().get(code)
        if iid is None:
            raise ValueError(f"모르는 종목: {code}")
        return self.executor.send(decision_id, iid, code, side, qty, price, order_type)

    def cancel_open(self) -> int:
        return self.executor.cancel_open()

    def resume_after_restart(self) -> list[TradeOrder]:
        return self.executor.reconcile_unknown()

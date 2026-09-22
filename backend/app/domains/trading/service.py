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
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import (
    BrokerRejected,
    BrokerSendFailed,
    BrokerUnavailable,
    InvalidState,
    OrdersBlocked,
    Precondition,
)
from app.domains.risk.service import GATE_REASONS
from app.domains.trading.crud import TradingCrud
from app.domains.trading.models import Fill, Reconciliation, ReconciliationDiff, TradeOrder
from app.domains.trading.ports import Balance, BrokerOrder, EquitySnapshot, OrderBrokerPort, OrderRequest

CLOSED_STATUSES = ("filled", "partial", "cancelled")  # 여기 오면 끝. rejected는 다시 판정한다
INDEX_ETF_CODE = "069500"  # KODEX 200. StrategyConfig에 지수 종목 칸이 없어 여기 둔다 (CODE-001 되먹임)
CASH_TOLERANCE = 10_000  # 예수금 차이 허용. 수수료·세금이 추정값이라 한 푼까지 맞지는 않는다
MAX_SELL_ATTEMPTS = 10  # 한 판단에서 같은 종목을 다시 파는 횟수 상한


def _attempt_of(idem_key: str) -> int:
    """멱등 키의 재시도 번호. `1:005930:sell#2` → 2."""
    return int(idem_key.rsplit("#", 1)[1]) if "#" in idem_key else 0


@dataclass
class ExecutionResult:
    sold: list[str] = field(default_factory=list)
    bought: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)  # 거래정지라 못 판 종목
    skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"매도 {len(self.sold)}", f"매수 {len(self.bought)}"]
        if self.held:
            parts.append(f"보류 {len(self.held)}({', '.join(self.held)})")
        if self.skipped:
            parts.append("건너뜀 " + ", ".join(f"{c}:{r}" for c, r in self.skipped.items()))
        if self.errors:
            parts.append("오류 " + "; ".join(self.errors))
        return " · ".join(parts)


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
        attempt: int = 0,
    ) -> TradeOrder:
        """같은 (판단, 종목, 방향, 시도)으로 두 번 불러도 증권사 호출은 한 번이다."""
        key = f"{decision_id}:{code}:{side}" + (f"#{attempt}" if attempt else "")
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
        price = round(amount / delta)
        self.crud.add_fill(
            Fill(
                order_id=o.id,
                qty=delta,
                price=price,
                fee=int(amount * FEE_RATE),
                tax=int(amount * TAX_RATE) if o.side == "sell" else 0,
                filled_at=self._now(),
                broker_fill_no=f"{prefix}{st.filled_qty}",  # 누적 수량이 키. 같은 값이면 같은 체결이다
            )
        )
        self._apply_position(o, delta, price)
        self.s.commit()

    def _apply_position(self, o: TradeOrder, qty: int, price: int) -> None:
        """체결 하나를 보유에 반영한다. 매수는 평균 단가를 다시 계산하고, 매도는 수량만 줄인다."""
        p = self.crud.position(o.instrument_id)
        held = p.qty if p else 0
        if o.side == "buy":
            new_qty = held + qty
            avg = round(((p.avg_cost * held if p else 0) + price * qty) / new_qty)
            self.crud.upsert_position(
                o.instrument_id, new_qty, avg, self._now(), "bot", first_on=self.clock.today().isoformat()
            )
        else:
            self.crud.upsert_position(
                o.instrument_id, max(held - qty, 0), p.avg_cost if p else price, self._now(), "bot"
            )

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
    """판단 실행, 주문 하나, 계좌 대조 (QBOT-MS-001 TradingService.execute)."""

    def __init__(
        self,
        session: Session,
        broker: OrderBrokerPort,
        gate,
        clock: Clock,
        instrument_ids: Callable[[], dict[str, int]],
        mode: str = "paper",
        plan_fn: Callable[[object], object] | None = None,
        price_fn: Callable[[str], int] | None = None,
        halted_codes_fn: Callable[[], set[str]] | None = None,
        state_fn: Callable[[], dict] | None = None,
        ensure_instrument_fn: Callable[[str], int] | None = None,
        block_orders_fn: Callable[[str], None] | None = None,
        unblock_orders_fn: Callable[[], None] | None = None,
        notify: Callable[[str, str], None] | None = None,
        index_etf: str = INDEX_ETF_CODE,
        **executor_kwargs,
    ) -> None:
        self.s = session
        self.crud = TradingCrud(session)
        self.broker = broker
        self.clock = clock
        self.instrument_ids = instrument_ids
        self.mode = mode
        self.plan_fn = plan_fn
        self.price_fn = price_fn or (lambda code: 0)
        self.halted_codes_fn = halted_codes_fn or (lambda: set())
        self.state_fn = state_fn or (lambda: {})
        self.ensure_instrument_fn = ensure_instrument_fn
        self.block_orders_fn = block_orders_fn or (lambda reason: None)
        self.unblock_orders_fn = unblock_orders_fn or (lambda: None)
        self.notify = notify
        self.index_etf = index_etf
        self._by_id: dict[int, str] | None = None
        self.executor = OrderExecutor(
            session, broker, gate, clock, self.equity_snapshot, self.code_of, **executor_kwargs
        )

    def _now(self) -> str:
        return self.clock.now().isoformat(timespec="seconds")

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

    # ----- 판단 실행 (QBOT-SEQ-001 SEQ-4) -----
    def execute(self, decision) -> ExecutionResult:
        """월말 판단을 주문으로 옮긴다. 매도 먼저, 매도 대금이 들어온 계좌로 매수."""
        if decision.status not in ("pending", "running", "partial"):
            raise InvalidState(f"{decision.status} 상태의 판단은 실행할 수 없다")
        state = self.state_fn()
        if state.get("halted"):
            raise OrdersBlocked("정지 상태다. 재개한 뒤에 실행한다")
        if state.get("orders_blocked"):
            raise OrdersBlocked("주문이 멈춰 있다. reconcile_accept 로 정리해야 실행할 수 있다")
        rec = self.reconcile()
        if rec.result == "mismatch":
            raise OrdersBlocked("계좌가 기록과 다르다. 확인 뒤 reconcile_accept 로 정리해야 주문이 나간다")
        if self.plan_fn is None:
            raise Precondition("판단 계획을 읽을 수 없다")
        plan = self.plan_fn(decision)
        res = ExecutionResult()
        decision.status = "running"
        self.s.commit()

        ids = self.instrument_ids()
        held = self._held_by_code()
        targets = set(plan.picks) | ({self.index_etf} if plan.index_weight > 0 else set())
        halted = self.halted_codes_fn()

        for code, qty in sorted(held.items()):
            if code in targets or qty <= 0:
                continue
            if code in halted:  # 오늘은 못 판다. 행만 남기고 retry_held_sells가 매일 다시 본다
                self._hold_sell(decision.id, ids.get(code), code, qty)
                res.held.append(code)
                continue
            self._try(
                res,
                code,
                "sell",
                lambda c=code, q=qty: self.executor.send(decision.id, ids[c], c, "sell", q, self.price_fn(c)),
            )

        bal = self.broker.balance()
        cash = bal.cash
        n = max(plan.n_holdings, 1)
        budget = int(bal.total_equity * (1 - plan.index_weight) / n)

        for code in plan.picks:  # 점수 순
            if code in held and held[code] > 0:
                continue
            if code not in ids:
                res.skipped[code] = "unknown"
                continue
            price = self.price_fn(code)
            if price <= 0:
                res.skipped[code] = "no_price"
                continue
            qty = min(budget, cash) // price
            if qty <= 0:
                res.skipped[code] = "cash"
                continue
            if self._try(
                res,
                code,
                "buy",
                lambda c=code, q=int(qty), p=price: self.executor.send(decision.id, ids[c], c, "buy", q, p),
            ):
                cash -= int(qty) * price

        if plan.index_weight > 0 and plan.index_rebalance:
            self._rebalance_index(decision.id, ids, bal, plan, res, cash)

        # 관문에 막힌 것이 하나라도 있으면 끝난 것이 아니다. done은 다시 실행할 수 없다
        refused = [c for c, r in res.skipped.items() if r in GATE_REASONS]
        decision.status = "partial" if (res.held or res.errors or refused) else "done"
        self.s.commit()
        if self.notify:
            self.notify("fill", f"{decision.asof} 실행: {res.summary()}")
        return res

    def _held_by_code(self) -> dict[str, int]:
        """보유를 종목 코드로 읽는다. 코드를 못 찾는 행은 대조가 '기록에 없는 종목'으로 잡게 남겨 둔다."""
        out = {}
        for p in self.crud.positions():
            code = self.code_of(p.instrument_id)
            if code is not None:
                out[code] = p.qty
        return out

    def _try(self, res: ExecutionResult, code: str, side: str, send) -> bool:
        """주문 하나를 보내고 결과를 모은다. 하나가 실패해도 나머지는 계속한다."""
        try:
            o = send()
        except Exception as e:  # noqa: BLE001 - 종목 하나의 실패로 리밸런싱 전체를 멈추지 않는다
            res.errors.append(f"{code} {side}: {e}")
            return False
        if o.status in ("filled", "partial"):
            left = o.qty - self.crud.filled_qty(o.id)
            if side == "sell" and left > 0:
                # 일부만 팔렸다. 남은 수량을 보류로 남겨 다음 날 마저 판다
                self._hold_sell(o.decision_id, o.instrument_id, code, left)
                res.held.append(code)
                return True
            (res.sold if side == "sell" else res.bought).append(code)
            return True
        res.skipped[code] = o.reject_reason or o.status
        return False

    def _hold_sell(self, decision_id: int, instrument_id: int | None, code: str, qty: int) -> None:
        """못 판 수량을 보류로 남긴다. 이미 끝난 주문 행은 건드리지 않고 다음 시도 번호를 쓴다."""
        if instrument_id is None or qty <= 0:
            return
        for attempt in range(MAX_SELL_ATTEMPTS):
            key = f"{decision_id}:{code}:sell" + (f"#{attempt}" if attempt else "")
            o = self.crud.order_by_key(key)
            if o is None:
                self.crud.add_order(
                    TradeOrder(
                        idem_key=key,
                        decision_id=decision_id,
                        instrument_id=instrument_id,
                        side="sell",
                        qty=qty,
                        order_type="market",
                        status="held",
                    )
                )
                break
            if o.status not in CLOSED_STATUSES:
                o.status, o.qty = "held", qty
                break
        self.s.commit()

    def _rebalance_index(self, decision_id, ids, bal, plan, res: ExecutionResult, cash: int) -> None:
        """지수 부분을 목표 비중에 맞춘다. 모자라면 사고 넘치면 판다."""
        code = self.index_etf
        if code not in ids:
            res.skipped[code] = "unknown"
            return
        price = self.price_fn(code)
        if price <= 0:
            res.skipped[code] = "no_price"
            return
        have = bal.holdings[code].value if code in bal.holdings else 0
        gap = int(bal.total_equity * plan.index_weight) - have
        if gap > 0:
            qty = min(gap, cash) // price
            if qty > 0:
                self._try(
                    res,
                    code,
                    "buy",
                    lambda q=int(qty): self.executor.send(decision_id, ids[code], code, "buy", q, price),
                )
        elif -gap >= price:
            qty = min(-gap // price, bal.holdings[code].qty)
            if qty > 0:
                self._try(
                    res,
                    code,
                    "sell",
                    lambda q=int(qty): self.executor.send(decision_id, ids[code], code, "sell", q, price),
                )

    def retry_held_sells(self) -> list[TradeOrder]:
        """거래정지·부분 체결로 못 판 수량을 매일 다시 시도한다 (UC-A3)."""
        halted, out, errors = self.halted_codes_fn(), [], []
        for o in self.crud.orders_by_status("held"):
            code = self.code_of(o.instrument_id)
            if code is None or code in halted:
                continue
            p = self.crud.position(o.instrument_id)
            qty = min(o.qty, p.qty if p else 0)
            if qty <= 0:  # 이미 없는 것은 팔 수 없다
                o.status, o.closed_at = "cancelled", self._now()
                self.s.commit()
                continue
            try:
                out.append(
                    self.executor.send(
                        o.decision_id,
                        o.instrument_id,
                        code,
                        "sell",
                        qty,
                        self.price_fn(code),
                        attempt=_attempt_of(o.idem_key),
                    )
                )
            except Exception as e:  # noqa: BLE001 - 하나가 막혀도 나머지 보류분은 시도한다
                errors.append(f"{code}: {e}")
        if errors and self.notify:
            self.notify("error", "보류 매도 재시도 실패: " + "; ".join(errors))
        return out

    # ----- 계좌 대조 (QBOT-SCN-001 S13) -----
    def reconcile(self) -> Reconciliation:
        """계좌와 기록을 맞춰 본다. 기록을 고치지는 않는다. 다르면 주문을 멈춘다."""
        bal = self.broker.balance()
        ids = self.instrument_ids()
        bot = self._held_by_code()
        broker_q = {c: h.qty for c, h in bal.holdings.items()}
        diffs, unknown = [], []
        for code in sorted(set(bot) | set(broker_q)):
            bq, kq = bot.get(code, 0), broker_q.get(code, 0)
            if bq == kq:
                continue
            if code in ids:
                diffs.append(ReconciliationDiff(instrument_id=ids[code], bot_qty=bq, broker_qty=kq))
            else:  # 우리가 모르는 종목이 계좌에 있다. 종목표에 없으니 diff 행으로 못 남긴다
                unknown.append(f"{code}({kq}주)")

        last = self.crud.last_settled()
        # 지난번 맞춘 시점의 계좌 현금에 그 뒤 우리 체결을 더하면 지금 현금이 나와야 한다
        bot_cash = bal.cash if last is None else last.broker_cash + self.crud.cash_flow_after(last.last_fill_id)
        tolerance = max(CASH_TOLERANCE, int(bal.total_equity * 0.01))
        cash_off = abs(bot_cash - bal.cash) > tolerance
        reason = []
        if unknown:
            reason.append("기록에 없는 종목: " + ", ".join(unknown))
        if cash_off:
            reason.append(f"예수금 차이 {bot_cash - bal.cash:+,}원")
        result = "mismatch" if (diffs or unknown or cash_off) else "ok"
        rec = self.crud.add_reconciliation(
            Reconciliation(
                checked_at=self._now(),
                mode=self.mode,
                bot_cash=bot_cash,
                broker_cash=bal.cash,
                result=result,
                last_fill_id=self.crud.max_fill_id(),
                reason="; ".join(reason)[:200] or None,
            ),
            diffs,
        )
        if result == "mismatch":
            self.block_orders_fn("계좌 불일치")
            if self.notify:
                self.notify("error", f"계좌 불일치. 주문을 멈춘다. {self.describe_reconciliation(rec)}")
        self.s.commit()
        return rec

    def describe_reconciliation(self, rec: Reconciliation) -> str:
        lines = [f"대조 {rec.checked_at} → {rec.result}"]
        for d in self.crud.diffs_of(rec.id):
            lines.append(f"  {self.code_of(d.instrument_id)}: 기록 {d.bot_qty}주 / 계좌 {d.broker_qty}주")
        if rec.reason:
            lines.append("  " + rec.reason)
        if rec.result == "ok":
            lines.append(f"  예수금 {rec.broker_cash:,}원")
        return "\n".join(lines)

    def accept_reconciliation(self, reason: str, command_id: int | None = None) -> Reconciliation:
        """계좌를 옳다고 보고 보유 기록을 덮어쓴다 (UC-H6). 사람이 이유를 남겨야 한다."""
        rec = self.crud.last_mismatch()
        if rec is None:
            raise Precondition("정리할 불일치 대조가 없다")
        bal = self.broker.balance()
        ids, now = self.instrument_ids(), self._now()
        for code, h in bal.holdings.items():
            iid = ids.get(code)
            if iid is None and self.ensure_instrument_fn is not None:
                iid = self.ensure_instrument_fn(code)  # 종목표에 없으면 만들어서라도 기록한다
                self._by_id = None
            if iid is not None:
                self.crud.upsert_position(iid, h.qty, h.avg_cost, now, "reconcile")
        for p in self.crud.positions():
            if self.code_of(p.instrument_id) not in bal.holdings:
                self.crud.upsert_position(p.instrument_id, 0, p.avg_cost, now, "reconcile")
        rec.resolution, rec.resolved_at = "accepted", now
        rec.resolved_by_command_id = command_id
        rec.reason = f"{reason[:150]} / {rec.reason or ''}"[:200]
        self.unblock_orders_fn()
        self.s.commit()
        return rec

    def positions(self) -> list[dict]:
        """계좌 기준 보유 현황. 매수일은 우리 기록에서 가져온다."""
        bal = self.broker.balance()
        ids, out = self.instrument_ids(), []
        for code, h in sorted(bal.holdings.items()):
            p = self.crud.position(ids[code]) if code in ids else None
            cost = h.avg_cost * h.qty
            out.append(
                {
                    "code": code,
                    "qty": h.qty,
                    "avg_cost": h.avg_cost,
                    "price": round(h.value / h.qty) if h.qty else 0,
                    "value": h.value,
                    "pnl": h.value - cost,
                    "pnl_pct": (h.value / cost - 1) if cost else 0.0,
                    "first_bought_on": p.first_bought_on if p else None,
                }
            )
        return out

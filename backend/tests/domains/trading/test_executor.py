"""OrderExecutor.send · reconcile_unknown (QBOT-MS-001 테스트 관점)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.clock import KST, FrozenClock
from app.core.errors import BrokerSendFailed, BrokerUnavailable
from app.domains.trading.crud import TradingCrud
from app.domains.trading.models import TradeOrder
from app.domains.trading.ports import BrokerOrder
from app.domains.trading.service import OrderExecutor
from tests.domains.trading.fakes import PRICE, AllowGate, FakeOrderBroker

CODE = "005930"


def _row(session, broker_no, qty):
    o = TradeOrder(
        idem_key="7:x:buy",
        decision_id=1,
        instrument_id=1,
        side="buy",
        qty=qty,
        order_type="market",
        status="sent",
        broker_order_no=broker_no,
    )
    session.add(o)
    session.commit()
    return o


def make(session, broker=None, gate=None, **kw):
    broker = broker or FakeOrderBroker()
    ex = OrderExecutor(
        session,
        broker,
        gate or AllowGate(),
        FrozenClock(datetime(2026, 9, 22, 9, 5, tzinfo=KST)),
        snapshot_fn=lambda: None,
        code_of=lambda iid: CODE if iid == 1 else None,
        sleep=lambda _: None,
        max_polls=kw.pop("max_polls", 2),
        **kw,
    )
    return ex, broker


def test_send_fills_and_calls_broker_once(session):
    ex, broker = make(session)
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    again = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "filled" and again.id == o.id
    assert len(broker.placed) == 1  # 같은 키로 두 번 불러도 전송은 한 번
    assert TradingCrud(session).filled_qty(o.id) == 3


def test_fee_and_tax_only_on_sell(session):
    ex, _ = make(session)
    buy = ex.send(1, 1, CODE, "buy", 2, PRICE)
    sell = ex.send(1, 1, CODE, "sell", 2, PRICE)
    crud = TradingCrud(session)
    assert crud.fills_of(buy.id)[0].tax == 0
    assert crud.fills_of(sell.id)[0].tax == int(2 * PRICE * 0.0018)


def test_transport_failure_leaves_unknown(session):
    ex, broker = make(session, FakeOrderBroker(fill_plan=["boom"]))
    with pytest.raises(BrokerSendFailed):
        ex.send(1, 1, CODE, "buy", 3, PRICE)
    o = TradingCrud(session).order_by_key(f"1:{CODE}:buy")
    assert o.status == "unknown" and o.broker_order_no is None


def test_unknown_without_broker_order_goes_back_to_planned(session):
    ex, broker = make(session, FakeOrderBroker(fill_plan=["boom"]))
    with pytest.raises(BrokerSendFailed):
        ex.send(1, 1, CODE, "buy", 3, PRICE)
    out = ex.reconcile_unknown()
    assert [o.status for o in out] == ["planned"]  # 증권사에 없으면 전송되지 않은 것


def test_reconcile_matches_only_one_of_two_identical_orders(session):
    broker = FakeOrderBroker(fill_plan=["boom"])
    ex, _ = make(session, broker)
    with pytest.raises(BrokerSendFailed):
        ex.send(1, 1, CODE, "buy", 3, PRICE)
    broker.extra_today = [
        BrokerOrder("2001", CODE, "buy", 3, 3, PRICE, raw_no="2001"),
        BrokerOrder("2002", CODE, "buy", 3, 3, PRICE, raw_no="2002"),
    ]
    broker.orders = list(broker.extra_today)
    broker.extra_today = []
    out = ex.reconcile_unknown()
    assert len(out) == 1 and out[0].broker_order_no == "2001" and out[0].status == "filled"


def test_gate_rejection_is_recorded_without_broker_call(session):
    ex, broker = make(session, gate=AllowGate("halted"))
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "rejected" and o.reject_reason == "halted" and broker.placed == []


def test_broker_rejection_is_not_an_exception(session):
    ex, _ = make(session, FakeOrderBroker(fill_plan=["reject"]))
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "rejected" and "40240000" in o.reject_reason


def test_gate_rejection_can_be_retried_after_the_reason_clears(session):
    """정지 때문에 거부된 주문이 재개 뒤에도 막혀 있으면 그 달 리밸런싱이 통째로 비어 버린다."""
    ex, broker = make(session, gate=AllowGate("halted"))
    first = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert first.status == "rejected"
    ex.gate = AllowGate()  # 재개
    again = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert again.id == first.id and again.status == "filled" and len(broker.placed) == 1


def test_rate_limit_puts_the_order_back_to_planned(session):
    """초당 한도 초과는 확실히 미체결이다. rejected로 닫아 버리면 그 주문은 영영 안 나간다."""
    broker = FakeOrderBroker(fill_plan=["busy"])
    ex, _ = make(session, broker)
    with pytest.raises(BrokerUnavailable):
        ex.send(1, 1, CODE, "buy", 3, PRICE)
    o = TradingCrud(session).order_by_key(f"1:{CODE}:buy")
    assert o.status == "planned" and o.broker_order_no is None
    ex.send(1, 1, CODE, "buy", 3, PRICE)  # 다음 호출에서 다시 나간다
    assert TradingCrud(session).order_by_key(f"1:{CODE}:buy").status == "filled"


def test_no_retry_while_the_original_order_is_still_alive(session):
    """취소가 확인되지 않았는데 남은 수량을 또 내면 두 번 산다."""
    broker = FakeOrderBroker(default_fill="none", cancel_works=False)
    ex, _ = make(session, broker, max_retries=1)
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "sent" and len(broker.placed) == 1  # 재주문 없음


def test_partial_fill_prices_only_the_new_quantity(session):
    """증권사 평균가는 누적이다. 그대로 쓰면 체결 금액 합이 실제와 어긋난다."""
    broker = FakeOrderBroker(default_fill="none")
    ex, _ = make(session, broker, max_retries=0)
    no = broker.place_order_for_test(CODE, "buy", 2, filled=1, avg=10_000)
    ex._record_fills(_row(session, no, 2), BrokerOrder(no, CODE, "buy", 2, 1, 10_000))
    o = TradingCrud(session).order_by_key("7:x:buy")
    ex._record_fills(o, BrokerOrder(no, CODE, "buy", 2, 2, 15_000))  # 누적 평균 15,000 = 총 30,000
    fills = TradingCrud(session).fills_of(o.id)
    assert [(f.qty, f.price) for f in fills] == [(1, 10_000), (1, 20_000)]  # 두 번째는 2만원에 샀다
    assert sum(f.qty * f.price for f in fills) == 30_000


def test_unfilled_is_cancelled_when_retries_exhausted(session):
    ex, broker = make(session, FakeOrderBroker(default_fill="none"), max_retries=0)
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "cancelled" and broker.cancelled == ["1001"]


def test_partial_fill_retries_remaining_quantity(session):
    broker = FakeOrderBroker(fill_plan=["half", "full"])
    ex, _ = make(session, broker, max_retries=1)
    o = ex.send(1, 1, CODE, "buy", 4, PRICE)
    assert o.retry_count == 1
    assert [r.qty for r in broker.placed] == [4, 2]  # 남은 2주만 다시 낸다
    assert o.status == "filled" and TradingCrud(session).filled_qty(o.id) == 4


def test_same_cumulative_fill_is_not_recorded_twice(session):
    ex, broker = make(session, max_polls=3)
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    ex._record_fills(o, broker.order_status(o.broker_order_no))
    assert len(TradingCrud(session).fills_of(o.id)) == 1


def test_cancel_open_cancels_sent_orders(session):
    """정지(halt)가 부른다. 미체결만 취소하고 이미 체결된 것은 filled로 닫는다."""
    broker = FakeOrderBroker(default_fill="none")
    ex, _ = make(session, broker)
    no = broker.place_order_for_test(CODE, "buy", 3)
    filled_no = broker.place_order_for_test(CODE, "sell", 2, filled=2)
    for key, side, qty, bno in [("9:a:buy", "buy", 3, no), ("9:b:sell", "sell", 2, filled_no)]:
        session.add(
            TradeOrder(
                idem_key=key,
                decision_id=1,
                instrument_id=1,
                side=side,
                qty=qty,
                order_type="market",
                status="sent",
                broker_order_no=bno,
            )
        )
    session.commit()
    assert ex.cancel_open() == 1  # 미체결 1건만 취소로 센다
    crud = TradingCrud(session)
    assert [o.status for o in crud.orders_by_status("cancelled")] == ["cancelled"]
    assert [o.status for o in crud.orders_by_status("filled")] == ["filled"]
    assert broker.cancelled == [no]


def test_gate_rejection_overwrites_a_planned_row(session):
    """전송 전에 죽어 planned로 남은 주문을 관문이 거부하면 그 행이 거부로 바뀌어야 한다."""
    ex, broker = make(session)
    session.add(
        TradeOrder(
            idem_key=f"1:{CODE}:buy",
            decision_id=1,
            instrument_id=1,
            side="buy",
            qty=3,
            order_type="market",
            status="planned",
        )
    )
    session.commit()
    ex.gate = AllowGate("halted")
    o = ex.send(1, 1, CODE, "buy", 3, PRICE)
    assert o.status == "rejected" and o.reject_reason == "halted" and broker.placed == []


def test_cancel_open_closes_planned_orders(session):
    ex, _ = make(session)
    session.add(
        TradeOrder(
            idem_key="8:a:buy",
            decision_id=1,
            instrument_id=1,
            side="buy",
            qty=1,
            order_type="market",
            status="planned",
        )
    )
    session.commit()
    assert ex.cancel_open() == 1
    assert TradingCrud(session).order_by_key("8:a:buy").status == "cancelled"


def test_yesterdays_broker_number_does_not_block_todays_match(session):
    """증권사 주문번호는 날마다 새로 매겨진다. 어제 같은 번호를 썼다고 오늘 짝을 놓치면 중복 주문이 난다."""
    broker = FakeOrderBroker(fill_plan=["boom"])
    ex, _ = make(session, broker)
    session.add(
        TradeOrder(
            idem_key="0:old:buy",
            decision_id=1,
            instrument_id=1,
            side="buy",
            qty=3,
            order_type="market",
            status="filled",
            broker_order_no="2001",
            sent_at="2026-09-21T09:10:00+09:00",  # 어제
        )
    )
    session.commit()
    with pytest.raises(BrokerSendFailed):
        ex.send(1, 1, CODE, "buy", 3, PRICE)
    broker.orders = [BrokerOrder("2001", CODE, "buy", 3, 3, PRICE, raw_no="2001")]
    out = ex.reconcile_unknown()
    assert out[0].status == "filled" and out[0].broker_order_no == "2001"

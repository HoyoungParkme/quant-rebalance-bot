"""종목별 손절 (QBOT-PRD-001 R6, QBOT-MS-001 TradingService.mark_stop_losses, QBOT-SCN-001 S14)."""

from __future__ import annotations

from datetime import date

from app.domains.trading.models import Position
from tests.domains.trading.fakes import PRICE, FakeOrderBroker
from tests.domains.trading.helpers import build

TODAY = date(2026, 9, 23)


def _pos(w, code):
    return w.session.get(Position, w.svc.crud.position(w.svc.instrument_ids()[code]).id)


def test_marks_at_minus_15_percent_not_above(session):
    """평단 50,000원이면 42,500원(-15%)에서 표시되고 42,501원에서는 안 된다."""
    w = build(session, holdings={"000001": 2, "000002": 2}, picks=["000001", "000002"])
    marks = w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.85, "000002": PRICE * 0.85 + 1})
    assert [m["code"] for m in marks] == ["000001"]
    assert _pos(w, "000001").stop_loss_on == "2026-09-23" and _pos(w, "000002").stop_loss_on is None


def test_index_etf_is_never_marked(session):
    w = build(session, holdings={"069500": 3}, picks=[], index_weight=0.2)
    assert w.svc.mark_stop_losses(TODAY, {"069500": PRICE * 0.7}) == []  # -30%라도 지수는 고정 비중


def test_marking_twice_does_not_repeat(session):
    w = build(session, holdings={"000001": 2}, picks=["000001"])
    assert len(w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.8})) == 1
    assert w.svc.mark_stop_losses(date(2026, 9, 24), {"000001": PRICE * 0.7}) == []  # 알림 중복 없음
    assert _pos(w, "000001").stop_loss_on == "2026-09-23"


def test_missing_close_is_skipped(session):
    w = build(session, holdings={"000001": 2}, picks=["000001"])
    assert w.svc.mark_stop_losses(TODAY, {}) == []  # 거래정지 등으로 종가가 없으면 건너뛴다


def test_execute_sells_a_marked_target_and_does_not_rebuy_it(session):
    """손절 표시된 종목은 목표에 있어도 팔고, 같은 판단에서 다시 사지 않는다. 그 자리는 현금이다."""
    w = build(session, holdings={"000001": 2}, cash=PRICE * 5, picks=["000001", "000002"], n_holdings=2)
    w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.8})  # 판단 기준일(2026-08-31) 뒤의 표시
    res = w.svc.execute(w.decision)
    assert res.sold == ["000001"] and "000001" not in res.bought
    assert res.skipped.get("000001") == "stop_loss" and "000002" in res.bought
    assert w.qty("000001") == 0


def test_sell_stop_losses_without_a_pending_decision(session):
    """대기 판단이 없는 날 08:40: 손절 종목만 동시호가에 걸고, 09:05 확인에서 체결을 기록한다."""
    broker = FakeOrderBroker(fill_plan=["none"])
    w = build(session, holdings={"000001": 2, "000002": 2}, picks=["000001", "000002"], broker=broker)
    w.decision.status = "done"
    session.commit()
    w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.8})
    w.svc.sell_stop_losses(w.decision)
    assert [(r.code, r.side) for r in broker.placed] == [("000001", "sell")]  # 000002는 건드리지 않는다
    from dataclasses import replace

    broker.orders[0] = replace(broker.orders[0], filled_qty=2, avg_price=PRICE)
    broker.holdings.pop("000001")
    broker.cash += 2 * PRICE
    assert w.svc.confirm_open_orders() == ["000001"]
    assert w.qty("000001") == 0 and w.svc.stop_loss_codes() == set()


def test_buying_again_later_clears_the_old_mark(session):
    """다음 판단이 그 종목을 다시 사면 새 매입이다. 지난 손절 표시는 지운다."""
    w = build(session, holdings={}, cash=PRICE * 3, picks=["000001"], n_holdings=1)
    pid = w.svc.instrument_ids()["000001"]
    w.svc.crud.upsert_position(pid, 0, PRICE, "2026-08-01T00:00:00+09:00", "bot").stop_loss_on = "2026-08-20"
    session.commit()
    res = w.svc.execute(w.decision)  # 판단 기준일 2026-08-31 > 표시일 → 막지 않는다
    assert res.bought == ["000001"] and _pos(w, "000001").stop_loss_on is None


def test_mark_on_the_decision_date_blocks_rebuy(session):
    """월말 20:10 점검의 표시는 21:00 판단과 날짜가 같다. 그래도 되사지 않는다(08:40 팔고 09:05 되사기 방지)."""
    w = build(session, holdings={"000001": 2}, cash=PRICE * 5, picks=["000001", "000002"], n_holdings=2)
    w.svc.mark_stop_losses(date(2026, 8, 31), {"000001": PRICE * 0.8})  # 판단 기준일과 같은 날
    res = w.svc.execute(w.decision)
    assert res.sold == ["000001"] and res.skipped.get("000001") == "stop_loss" and w.qty("000001") == 0


def test_old_mark_sold_under_this_decision_blocks_rebuy(session):
    """기준일 전 표시(거래정지로 못 판 것)를 이 판단에서 팔았으면 이 판단에서 되사지 않는다."""
    w = build(session, holdings={"000001": 2}, cash=PRICE * 5, picks=["000001", "000002"], n_holdings=2)
    w.svc.mark_stop_losses(date(2026, 8, 20), {"000001": PRICE * 0.8})
    res = w.svc.execute(w.decision)
    assert res.sold == ["000001"] and "000001" not in res.bought


def test_cancelled_stop_sell_is_sent_again_next_day(session):
    """동시호가 손절 매도가 안 팔려 15:40에 취소되면, 다음 날 새 시도 번호로 다시 낸다(옛 키를 쓰면 안 판다)."""
    broker = FakeOrderBroker(fill_plan=["none"])
    w = build(session, holdings={"000001": 2}, picks=["000001"], broker=broker)
    w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.8})
    w.svc.execute(w.decision, phase="sell")
    assert w.svc.cancel_open() == 1  # 15:40
    w.svc.execute(w.decision, phase="sell")  # 다음 날 08:40
    assert [r.side for r in broker.placed] == ["sell", "sell"]


def test_no_second_stop_sell_while_a_held_sell_is_open(session):
    """거래정지로 보류된 매도가 있으면 손절 매도를 또 내지 않는다. 둘 다 나가면 같은 주식을 두 번 판다."""
    w = build(session, holdings={"000001": 2}, picks=["000001"], halted={"000001"})
    w.decision.status = "done"
    session.commit()
    w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.8})
    w.svc._hold_sell(w.decision.id, w.svc.instrument_ids()["000001"], "000001", 2)
    session.commit()
    res = w.svc.sell_stop_losses(w.decision)
    assert w.broker.placed == [] and res.held == []


def test_no_marks_while_orders_are_blocked(session):
    """계좌 불일치 중에는 표시하지 않는다. 분할 뒤 옛 평단으로 재면 가짜 손절이 된다."""
    w = build(session, holdings={"000001": 2}, picks=["000001"], state_fn=lambda: {"orders_blocked": True})
    assert w.svc.mark_stop_losses(TODAY, {"000001": PRICE * 0.5}) == []

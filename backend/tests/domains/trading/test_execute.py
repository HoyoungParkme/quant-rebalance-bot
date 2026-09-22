"""TradingService.execute — 판단을 주문으로 옮긴다 (QBOT-SEQ-001 SEQ-4)."""

from __future__ import annotations

import pytest

from app.core.errors import InvalidState, OrdersBlocked
from tests.domains.trading.fakes import PRICE
from tests.domains.trading.helpers import build


def test_sells_what_is_not_a_target_then_buys_with_that_money(session):
    """핵심: 매수 예산은 판단 시점 평가액이 아니라 매도가 반영된 실제 계좌에서 나온다."""
    w = build(session, holdings={"000001": 2}, cash=0, picks=["000002", "000003"], n_holdings=2)
    res = w.svc.execute(w.decision)
    assert res.sold == ["000001"] and res.bought == ["000002", "000003"]
    assert [r.side for r in w.broker.placed] == ["sell", "buy", "buy"]  # 매도가 먼저다
    assert w.qty("000001") == 0 and w.qty("000002") == 1 and w.qty("000003") == 1
    assert w.decision.status == "done"


def test_keeps_what_is_still_a_target(session):
    w = build(session, holdings={"000002": 1}, cash=PRICE, picks=["000002", "000003"], n_holdings=2)
    res = w.svc.execute(w.decision)
    assert res.sold == [] and res.bought == ["000003"]  # 000002는 그대로 둔다


def test_running_out_of_cash_skips_the_rest(session):
    """예산은 한 자리당 평가액/종목수다. 앞 종목을 사고 현금이 마르면 뒤는 건너뛴다."""
    w = build(session, holdings={}, cash=PRICE + PRICE // 2, picks=["000002", "000003"], n_holdings=1)
    res = w.svc.execute(w.decision)
    assert res.bought == ["000002"] and res.skipped == {"000003": "cash"}


def test_halted_stock_is_held_and_retried_later(session):
    w = build(session, holdings={"000001": 2}, picks=["000002"], halted={"000001"})
    res = w.svc.execute(w.decision)
    assert res.held == ["000001"] and w.decision.status == "partial"
    assert [r.side for r in w.broker.placed] == []  # 매도도 매수도 못 냈다(현금 0)
    held = w.svc.crud.orders_by_status("held")
    assert len(held) == 1 and held[0].qty == 2

    w.svc.halted_codes_fn = lambda: set()  # 다음 날 거래정지가 풀렸다
    out = w.svc.retry_held_sells()
    assert [o.status for o in out] == ["filled"] and w.qty("000001") == 0


def test_mismatch_blocks_the_whole_execution(session):
    """계좌가 기록과 다르면 한 주도 내지 않는다."""
    w = build(session, holdings={"000001": 2}, picks=["000002"])
    w.broker.hold("000001", 1)  # 사람이 앱에서 1주 팔았다
    with pytest.raises(OrdersBlocked):
        w.svc.execute(w.decision)
    assert w.broker.placed == [] and w.decision.status == "pending"


def test_finished_decision_cannot_run_again(session):
    w = build(session, picks=["000002"], cash=PRICE)
    w.svc.execute(w.decision)
    with pytest.raises(InvalidState):
        w.svc.execute(w.decision)


def test_rerunning_a_partial_decision_does_not_duplicate_orders(session):
    w = build(session, holdings={"000001": 2}, picks=["000002"], halted={"000001"}, cash=PRICE)
    w.svc.execute(w.decision)
    placed = len(w.broker.placed)
    w.svc.execute(w.decision)  # 보류 때문에 partial. 다시 돌려도 산 것을 또 사지 않는다
    assert len(w.broker.placed) == placed


def test_one_bad_stock_does_not_stop_the_rest(session):
    w = build(session, cash=2 * PRICE, picks=["999999", "000002"], n_holdings=2)
    res = w.svc.execute(w.decision)
    assert res.skipped == {"999999": "unknown"} and res.bought == ["000002"]


def test_index_part_is_bought_up_to_its_weight(session):
    w = build(session, cash=10 * PRICE, picks=[], index_weight=0.2, index_rebalance=True, n_holdings=2)
    res = w.svc.execute(w.decision)
    assert res.bought == ["069500"] and w.qty("069500") == 2  # 50만원의 20% = 10만원 = 2주


def test_index_part_is_trimmed_when_too_big(session):
    w = build(session, holdings={"069500": 5}, cash=0, picks=[], index_weight=0.2, index_rebalance=True, n_holdings=2)
    res = w.svc.execute(w.decision)
    assert res.sold == ["069500"] and w.qty("069500") == 1  # 25만원의 20% = 5만원 = 1주만 남긴다


def test_position_whose_code_is_gone_does_not_crash_execution(session):
    """종목표에서 사라진 보유 행이 있어도 나머지 리밸런싱은 돌아야 한다."""
    w = build(session, holdings={"000001": 2}, picks=["000002"])
    w.svc.crud.upsert_position(9999, 5, PRICE, "2026-08-31T15:30:00+09:00", "bot")  # 모르는 종목
    session.commit()
    res = w.svc.execute(w.decision)
    assert res.sold == ["000001"] and res.bought == ["000002"]


def test_blocked_orders_stop_execution_before_anything_moves(session):
    """대조가 막아 둔 상태에서 실행하면, 관문이 전부 거부해 '아무것도 안 한 done'이 된다."""
    w = build(session, holdings={"000001": 2}, picks=["000002"], state_fn=lambda: {"orders_blocked": True})
    with pytest.raises(OrdersBlocked):
        w.svc.execute(w.decision)
    assert w.broker.placed == [] and w.decision.status == "pending"


def test_halted_bot_does_not_execute(session):
    w = build(session, picks=["000002"], cash=PRICE, state_fn=lambda: {"halted": True})
    with pytest.raises(OrdersBlocked):
        w.svc.execute(w.decision)


def test_refused_orders_leave_the_decision_rerunnable(session):
    """관문이 거부한 판단을 done으로 닫으면 그 달 리밸런싱이 통째로 사라진다."""
    from app.domains.risk.service import GateVerdict

    w = build(session, cash=10 * PRICE, picks=["000002", "000003"])
    w.svc.executor.gate = type("G", (), {"check": lambda self, req, snap: GateVerdict(False, "reconcile")})()
    res = w.svc.execute(w.decision)
    assert res.bought == [] and w.decision.status == "partial"  # done이 아니다
    w.svc.executor.gate = type("G", (), {"check": lambda self, req, snap: GateVerdict(True, None)})()
    res2 = w.svc.execute(w.decision)  # 풀린 뒤 다시 돌릴 수 있다
    assert res2.bought == ["000002", "000003"] and w.decision.status == "done"


def test_partly_sold_stock_is_finished_the_next_day(session):
    """2주 중 1주만 팔렸으면 남은 1주도 팔아야 한다. 같은 멱등 키로는 다시 못 내므로 시도 번호를 붙인다."""
    from tests.domains.trading.fakes import FakeOrderBroker

    b = FakeOrderBroker(fill_plan=["half", "none"], cash=0)
    w = build(session, holdings={"000001": 2}, picks=[], broker=b)
    res = w.svc.execute(w.decision)
    assert res.held == ["000001"] and w.qty("000001") == 1 and w.decision.status == "partial"

    b.fill_plan, b.default_fill = [], "full"
    out = w.svc.retry_held_sells()
    assert [o.idem_key for o in out] == ["1:000001:sell#1"] and w.qty("000001") == 0


def test_retry_does_not_resend_more_than_we_hold(session):
    w = build(session, holdings={"000001": 2}, picks=[], halted={"000001"})
    w.svc.execute(w.decision)
    w.svc.crud.upsert_position(w.svc.instrument_ids()["000001"], 1, PRICE, "x", "reconcile")  # 대조로 1주가 됐다
    session.commit()
    w.svc.halted_codes_fn = lambda: set()
    out = w.svc.retry_held_sells()
    assert [r.qty for r in w.broker.placed] == [1] and out[0].status == "filled"


def test_one_failing_retry_does_not_block_the_others(session):
    w = build(session, holdings={"000001": 2, "000002": 2}, picks=[], halted={"000001", "000002"})
    w.svc.execute(w.decision)
    w.svc.halted_codes_fn = lambda: set()
    real = w.svc.executor.send

    def flaky(decision_id, iid, code, *a, **kw):
        if code == "000001":
            raise TimeoutError("증권사 응답 없음")
        return real(decision_id, iid, code, *a, **kw)

    w.svc.executor.send = flaky
    out = w.svc.retry_held_sells()
    assert [o.idem_key for o in out] == ["1:000002:sell"] and w.qty("000002") == 0

"""TradingService.reconcile / accept_reconciliation (QBOT-SCN-001 S13, UC-H6)."""

from __future__ import annotations

import pytest

from app.core.errors import Precondition
from app.domains.trading.ports import Balance, Holding
from tests.domains.trading.fakes import PRICE
from tests.domains.trading.helpers import build


def make(session, **kw):
    blocked, notes = [], []
    w = build(
        session,
        block_orders_fn=lambda reason: blocked.append(reason),
        unblock_orders_fn=lambda: blocked.clear(),
        notify=lambda kind, text: notes.append((kind, text)),
        **kw,
    )
    return w, blocked, notes


def test_matching_account_is_ok(session):
    w, blocked, notes = make(session, holdings={"000001": 2}, cash=PRICE)
    rec = w.svc.reconcile()
    assert rec.result == "ok" and blocked == [] and notes == []


def test_a_share_sold_in_the_app_is_caught(session):
    """사람이 증권사 앱에서 1주를 팔면 저녁 대조가 잡고 주문을 멈춘다."""
    w, blocked, notes = make(session, holdings={"000001": 2}, cash=PRICE)
    w.broker.hold("000001", 1)
    rec = w.svc.reconcile()
    assert rec.result == "mismatch" and blocked == ["계좌 불일치"]
    diffs = w.svc.crud.diffs_of(rec.id)
    assert [(d.bot_qty, d.broker_qty) for d in diffs] == [(2, 1)]
    assert notes and notes[0][0] == "error" and "000001" in notes[0][1]


def test_unknown_stock_in_the_account_is_reported(session):
    w, blocked, _ = make(session, cash=PRICE)
    w.broker.hold("111111", 3)  # 종목표에 없는 종목
    rec = w.svc.reconcile()
    assert rec.result == "mismatch" and "111111" in rec.reason and blocked


def test_cash_change_explained_by_our_own_fills_is_ok(session):
    """우리가 판 돈이 들어온 것은 불일치가 아니다."""
    w, blocked, _ = make(session, holdings={"000001": 2}, cash=0, picks=[], n_holdings=2)
    first = w.svc.reconcile()
    assert first.result == "ok"
    w.svc.send(1, "000001", "sell", 2, PRICE)  # 계좌 현금이 10만원 늘어난다
    rec = w.svc.reconcile()
    assert rec.result == "ok" and blocked == []


def test_cash_that_nobody_explains_is_caught(session):
    """입출금이나 앱 매매로 현금만 바뀌어도 잡아야 한다."""
    w, blocked, _ = make(session, holdings={"000001": 2}, cash=PRICE)
    w.svc.reconcile()
    w.broker.cash += 500_000  # 누군가 돈을 넣었다(또는 우리가 모르는 매도)
    rec = w.svc.reconcile()
    assert rec.result == "mismatch" and "예수금" in rec.reason and blocked


def test_small_cash_difference_is_tolerated(session):
    """수수료·세금은 추정값이라 한 푼까지 맞지 않는다."""
    w, blocked, _ = make(session, holdings={"000001": 2}, cash=PRICE)
    w.svc.reconcile()
    w.broker.cash += 300
    assert w.svc.reconcile().result == "ok" and blocked == []


def test_accept_overwrites_records_and_unblocks(session):
    w, blocked, _ = make(session, holdings={"000001": 2}, cash=PRICE)
    w.broker.hold("000001", 1)
    w.svc.reconcile()
    assert blocked
    rec = w.svc.accept_reconciliation("앱에서 직접 1주 팔았음")
    assert rec.resolution == "accepted" and "앱에서" in rec.reason and blocked == []
    assert w.qty("000001") == 1  # 계좌를 옳다고 보고 기록을 고쳤다
    assert w.svc.reconcile().result == "ok"


def test_accept_without_a_mismatch_is_refused(session):
    w, _, _ = make(session, cash=PRICE)
    with pytest.raises(Precondition):
        w.svc.accept_reconciliation("이유")


def test_position_gone_from_the_account_is_zeroed_on_accept(session):
    w, _, _ = make(session, holdings={"000001": 2}, cash=PRICE)
    w.broker.holdings.clear()
    w.svc.reconcile()
    w.svc.accept_reconciliation("전량 처분")
    assert w.qty("000001") == 0


def test_positions_view_uses_the_account(session):
    w, _, _ = make(session, holdings={"000001": 2}, cash=0)
    w.broker.holdings["000001"] = Holding("000001", 2, 40_000, 120_000)
    rows = w.svc.positions()
    assert rows[0]["code"] == "000001" and rows[0]["qty"] == 2
    assert rows[0]["price"] == 60_000 and rows[0]["pnl"] == 40_000
    assert rows[0]["first_bought_on"] == "2026-08-31"  # 매수일은 우리 기록에서


def test_broker_failure_does_not_pretend_the_account_is_empty(session):
    """잔고 조회가 실패했는데 '보유 0'으로 읽으면 전량 매도가 난다."""
    w, _, _ = make(session, holdings={"000001": 2}, cash=PRICE)

    def boom() -> Balance:
        raise TimeoutError("증권사 응답 없음")

    w.broker.balance = boom
    with pytest.raises(TimeoutError):
        w.svc.reconcile()


def test_mismatch_does_not_heal_itself_on_the_next_check(session):
    """불일치 행을 기준으로 삼으면 다음 대조가 '지금 계좌'에 다시 닻을 내려 저절로 ok가 된다."""
    w, blocked, _ = make(session, holdings={"000001": 2}, cash=PRICE)
    w.svc.reconcile()
    w.broker.cash += 500_000  # 설명 안 되는 입금
    assert w.svc.reconcile().result == "mismatch"
    assert w.svc.reconcile().result == "mismatch"  # 사람이 정리하기 전에는 계속 불일치다
    assert blocked


def test_unknown_stock_can_be_accepted_and_cleared(session):
    """종목표에 없는 보유를 정리할 길이 없으면 주문이 영원히 막힌다."""
    made = {}

    def ensure(code: str) -> int:
        made[code] = 99
        w.svc.instrument_ids = lambda: {**ids, code: 99}
        return 99

    w, blocked, _ = make(session, cash=PRICE)
    ids = dict(w.svc.instrument_ids())
    w.svc.ensure_instrument_fn = ensure
    w.broker.hold("111111", 3)
    assert w.svc.reconcile().result == "mismatch" and blocked
    w.svc.accept_reconciliation("증권사 배정 물량")
    assert made == {"111111": 99}
    assert w.svc.reconcile().result == "ok" and blocked == []

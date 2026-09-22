"""TradingService — 계좌 한 장면과 종목 해석 (D1 범위)."""

from __future__ import annotations

from datetime import datetime

from app.core.clock import KST, FrozenClock
from app.domains.risk.models import BotState
from app.domains.risk.service import OrderGate
from app.domains.trading.ports import Holding
from app.domains.trading.service import TradingService
from tests.domains.trading.fakes import PRICE, FakeOrderBroker

CODE = "005930"


def make(session, broker=None):
    broker = broker or FakeOrderBroker(holdings={CODE: Holding(CODE, 2, 40_000, 100_000)}, fixed_total=True)
    session.add(BotState(id=1, mode="paper", updated_at="2026-09-22T09:00:00+09:00"))
    session.commit()
    return (
        TradingService(
            session,
            broker,
            OrderGate(session),
            FrozenClock(datetime(2026, 9, 22, 10, 0, tzinfo=KST)),
            lambda: {CODE: 1},
            sleep=lambda _: None,
            max_polls=2,
        ),
        broker,
    )


def test_equity_snapshot_reads_broker_and_today_fills(session):
    svc, broker = make(session)
    s0 = svc.equity_snapshot()
    assert s0.total == 1_000_000 and s0.holdings == {CODE: 100_000} and s0.today_order_amount == 0
    svc.send(1, CODE, "buy", 1, PRICE)  # 보유 10만 + 5만 = 정확히 15%. 상한과 같으면 통과다
    s1 = svc.equity_snapshot()
    assert s1.today_order_amount == PRICE and s1.month_buy_amount == PRICE


def test_sell_does_not_count_as_month_buy(session):
    svc, _ = make(session)
    svc.send(1, CODE, "sell", 2, PRICE)
    s = svc.equity_snapshot()
    assert s.today_order_amount == 2 * PRICE and s.month_buy_amount == 0


def test_unknown_code_is_refused_before_any_order(session):
    svc, broker = make(session)
    try:
        svc.send(1, "999999", "buy", 1, PRICE)
        raise AssertionError("예외가 나야 한다")
    except ValueError as e:
        assert "모르는 종목" in str(e)
    assert broker.placed == []


def test_gate_sees_snapshot_from_broker(session):
    """비중 상한은 증권사 잔고 기준으로 판정된다."""
    broker = FakeOrderBroker(total=1_000_000, holdings={CODE: Holding(CODE, 2, 40_000, 100_000)}, fixed_total=True)
    svc, _ = make(session, broker)
    o = svc.send(1, CODE, "buy", 2, PRICE)  # 10만 보유 + 10만 매수 = 20% > 15%
    assert o.status == "rejected" and o.reject_reason == "concentration"

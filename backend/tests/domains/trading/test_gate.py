"""OrderGate.check — 거부 사유 8가지 (QBOT-MS-001 테스트 관점)."""

from __future__ import annotations

import pytest

from app.domains.risk.models import BotState
from app.domains.risk.service import OrderGate
from app.domains.trading.ports import EquitySnapshot, OrderRequest

CODE = "005930"


def snap(total=1_000_000, held=0, today=0, month=0):
    return EquitySnapshot(
        cash=total, total=total, holdings={CODE: held}, today_order_amount=today, month_buy_amount=month
    )


def state(session, **kw):
    row = BotState(id=1, mode=kw.pop("mode", "paper"), updated_at="2026-09-22T09:00:00+09:00", **kw)
    session.add(row)
    session.commit()
    return row


def buy(qty=1, price=100_000):
    return OrderRequest(CODE, "buy", qty, price)


def test_no_state_is_rejected(session):
    v = OrderGate(session).check(buy(), snap())
    assert v.allowed is False and v.reason == "no_state"


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"halted": 1}, "halted"),
        ({"orders_blocked": 1}, "reconcile"),
        ({"mode": "live"}, "no_gate"),
        ({"buy_suspended": 1}, "drawdown"),
    ],
)
def test_state_flags_reject(session, kw, reason):
    state(session, **kw)
    assert OrderGate(session).check(buy(), snap()).reason == reason


def test_concentration_limit(session):
    state(session)
    g = OrderGate(session, max_position_weight=0.15)
    assert g.check(buy(price=160_000), snap()).reason == "concentration"  # 16% > 15%
    assert g.check(buy(price=140_000), snap()).allowed
    assert g.check(buy(price=100_000), snap(held=60_000)).reason == "concentration"  # 보유분을 더해서 본다


def test_daily_cap(session):
    state(session)
    g = OrderGate(session, daily_order_cap_multiple=2.0)
    assert g.check(buy(price=100_000), snap(today=1_950_000)).reason == "daily_cap"
    assert g.check(buy(price=100_000), snap(today=1_800_000)).allowed


def test_first_month_cap(session):
    state(session, first_month_cap=300_000)
    g = OrderGate(session)
    assert g.check(buy(price=100_000), snap(month=250_000)).reason == "first_month"
    assert g.check(buy(price=100_000), snap(month=150_000)).allowed


def test_sell_skips_buy_only_rules(session):
    state(session, buy_suspended=1, first_month_cap=1)
    sell = OrderRequest(CODE, "sell", 1, 900_000)  # 비중 상한도 넘는 금액
    assert OrderGate(session).check(sell, snap()).allowed  # 매도는 5·6·8번을 건너뛴다


def test_sell_still_blocked_by_halt(session):
    state(session, halted=1)
    assert OrderGate(session).check(OrderRequest(CODE, "sell", 1, 1000), snap()).reason == "halted"


def test_zero_equity_rejects_buy(session):
    state(session)
    assert OrderGate(session).check(buy(), snap(total=0)).reason == "no_equity"

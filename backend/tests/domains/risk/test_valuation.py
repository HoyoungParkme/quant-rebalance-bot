"""ValuationRecorder — 일별 평가액과 손실 한도 (QBOT-MS-001 record_valuation)."""

from __future__ import annotations

from datetime import date, datetime

from app.core.clock import KST, FrozenClock
from app.domains.risk.models import BotState
from app.domains.risk.service import OrderGate, RiskService, ValuationRecorder
from app.domains.trading.ports import Balance, EquitySnapshot, Holding, OrderRequest

CLOCK = FrozenClock(datetime(2026, 9, 22, 18, 30, tzinfo=KST))


def make(session, total=1_000_000, cash=1_000_000, holdings=None, flow=0, max_drawdown=0.30):
    session.add(BotState(id=1, mode="paper", updated_at="2026-09-22T09:00:00+09:00"))
    session.commit()
    notes = []
    risk = RiskService(session, CLOCK, "paper")
    bal = Balance(cash, total, holdings or {})
    rec = ValuationRecorder(
        risk,
        lambda: bal,
        flow_fn=lambda day: flow,
        notify=lambda kind, text: notes.append((kind, text)),
        max_drawdown=max_drawdown,
    )
    return rec, risk, notes, bal


def test_first_record_seeds_principal_and_peak(session):
    rec, risk, _, _ = make(session, total=1_000_000)
    v = rec.record(date(2026, 9, 22))
    s = risk.state()
    assert s.principal == 1_000_000 and s.peak_equity == 1_000_000
    assert v.drawdown == 0.0 and v.pnl_vs_principal == 0.0


def test_deposit_moves_the_peak_with_it(session):
    """입금은 수익이 아니다. 고점과 원금을 같이 옮겨 고점 대비 0%가 되게 한다."""
    rec, risk, _, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 21))
    rec.flow_fn = lambda day: 1_000_000
    rec.balance_fn = lambda: Balance(2_000_000, 2_000_000, {})
    v = rec.record(date(2026, 9, 22))
    assert risk.state().peak_equity == 2_000_000  # 기준선이 입금만큼 올라간다
    assert risk.state().principal == 2_000_000
    assert v.drawdown == 0.0 and v.external_flow == 1_000_000


def test_drawdown_limit_suspends_buying_once_and_notifies(session):
    rec, risk, notes, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 21))
    rec.balance_fn = lambda: Balance(690_000, 690_000, {})
    v = rec.record(date(2026, 9, 22))
    assert round(v.drawdown, 2) == -0.31 and risk.state().buy_suspended == 1
    assert notes and notes[0][0] == "error" and "-31" in notes[0][1]
    notes.clear()
    rec.record(date(2026, 9, 22))  # 다시 기록해도 알림은 한 번만
    assert notes == []


def test_suspended_buying_is_enforced_by_the_gate(session):
    """한도에 걸리면 관문이 실제로 매수를 거부해야 한다. 기록만 하면 소용없다."""
    rec, _, _, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 21))
    rec.balance_fn = lambda: Balance(600_000, 600_000, {})
    rec.record(date(2026, 9, 22))
    snap = EquitySnapshot(cash=600_000, total=600_000, holdings={}, today_order_amount=0, month_buy_amount=0)
    gate = OrderGate(session)
    assert gate.check(OrderRequest("005930", "buy", 1, 10_000), snap).reason == "drawdown"
    assert gate.check(OrderRequest("005930", "sell", 1, 10_000), snap).allowed  # 매도는 막지 않는다


def test_index_part_is_recorded_separately(session):
    holdings = {"069500": Holding("069500", 3, 30_000, 100_000)}
    rec, _, _, _ = make(session, total=1_000_000, cash=400_000, holdings=holdings)
    v = rec.record(date(2026, 9, 22))
    assert v.index_value == 100_000 and v.cash == 400_000 and v.stock_value == 500_000


def test_same_day_is_updated_not_duplicated(session):
    rec, risk, _, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 22))
    rec.balance_fn = lambda: Balance(1_100_000, 1_100_000, {})
    v = rec.record(date(2026, 9, 22))
    assert v.total == 1_100_000 and len(risk.crud.valuations("2026-09-01", "2026-09-30")) == 1


def test_drawdown_still_works_after_a_deposit(session):
    """입금 뒤 폭락이 손실 한도에 걸려야 한다. 고점을 안 옮기면 영영 안 걸린다."""
    rec, risk, notes, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 21))
    rec.flow_fn = lambda day: 1_000_000
    rec.balance_fn = lambda: Balance(2_000_000, 2_000_000, {})
    rec.record(date(2026, 9, 22))
    rec.flow_fn = lambda day: 0
    rec.balance_fn = lambda: Balance(1_300_000, 1_300_000, {})  # 200만 → 130만 (-35%)
    v = rec.record(date(2026, 9, 23))
    assert round(v.drawdown, 2) == -0.35 and risk.state().buy_suspended == 1


def test_running_the_same_day_twice_does_not_double_count_a_deposit(session):
    """재시작이나 수동 재실행으로 저녁 작업이 두 번 돌 수 있다. 입금이 두 번 더해지면 안 된다."""
    rec, risk, _, _ = make(session, total=1_000_000)
    rec.record(date(2026, 9, 21))
    rec.flow_fn = lambda day: 500_000
    rec.balance_fn = lambda: Balance(1_500_000, 1_500_000, {})
    rec.record(date(2026, 9, 22))
    rec.record(date(2026, 9, 22))  # 같은 날 또
    s = risk.state()
    assert s.principal == 1_500_000 and s.peak_equity == 1_500_000

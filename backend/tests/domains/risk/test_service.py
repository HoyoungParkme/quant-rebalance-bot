"""RiskService (C2 범위): 정지·재개·상태 유지."""

from datetime import datetime

import pytest

from app.core.clock import FrozenClock
from app.core.errors import Precondition
from app.domains.risk.models import BotState
from app.domains.risk.service import RiskService

CLOCK = FrozenClock(datetime(2025, 5, 30, 18, 30))


def test_halt_persists_and_cancels_open_orders(session):
    calls = []
    svc = RiskService(session, CLOCK, "paper", cancel_open_orders=lambda: calls.append(1) or 3)
    s, cancelled = svc.halt("점검")
    assert s.halted == 1 and cancelled == 3
    assert session.get(BotState, 1).halted == 1  # 다시 읽어도 정지


def test_resume_requires_halted_and_reset_peak_when_drawdown(session):
    svc = RiskService(session, CLOCK, "paper")
    with pytest.raises(Precondition):
        svc.resume(False)
    st = svc.state()
    st.buy_suspended = 1
    with pytest.raises(Precondition):
        svc.resume(False)
    out = svc.resume(True, current_equity=1_234)
    assert out.buy_suspended == 0 and out.halted == 0 and out.peak_equity == 1_234 and out.peak_reset_at


def test_state_row_is_singleton(session):
    a = RiskService(session, CLOCK, "paper").state()
    b = RiskService(session, CLOCK, "paper").state()
    assert a.id == b.id == 1

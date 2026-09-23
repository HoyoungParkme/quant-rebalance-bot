"""DecisionService.decide_month_end / replay (QBOT-MS-001 테스트 관점)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.errors import DataNotReady
from app.domains.decision.crud import DecisionCrud
from app.domains.decision.models import Decision, Score
from app.domains.decision.service import DecisionService, Portfolio, seed_default_config
from app.domains.marketdata.models import IndexLevel, InstrumentStatus
from app.domains.marketdata.service import MarketDataService
from tests.domains.decision.helpers import seed_market

ASOF = date(2025, 5, 30)


def make(session, capital=3_000_000, notes=None, index_value=0):
    svc = DecisionService(
        session,
        MarketDataService(session),
        lambda: Portfolio(capital, index_value),
        "abc123",
        notifier=(lambda k, t: notes.append((k, t))) if notes is not None else None,
    )
    return svc


def test_decide_stores_pending_and_is_idempotent(session):
    seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    notes = []
    svc = make(session, notes=notes)
    d = svc.decide_month_end(ASOF, "paper")
    assert d.status == "pending" and d.code_version == "abc123"
    assert d.budget_per_slot == int(3_000_000 * 0.8 / 10)
    picks = DecisionCrud(session).picks(d.id)
    assert 1 <= len(picks) <= 10
    assert session.scalar(select(func.count()).select_from(Score)) <= 60
    assert notes and notes[0][0] == "order" and picks[0] in notes[0][1]
    d2 = svc.decide_month_end(ASOF, "paper")
    assert d2.id == d.id
    assert session.scalar(select(func.count()).select_from(Decision)) == 1


def test_data_not_ready_when_no_bars_on_asof(session):
    seed_market(session, asof="2025-05-29")
    seed_default_config(DecisionCrud(session), "2025-01-01")
    with pytest.raises(DataNotReady):
        make(session).decide_month_end(ASOF, "paper")


def test_replay_matches_stored_and_does_not_touch_real(session):
    seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    svc = make(session)
    real = svc.decide_month_end(ASOF, "paper")
    r1 = svc.replay(ASOF, "stored")
    r2 = svc.replay(ASOF, "stored")  # 재현은 여러 번 저장돼도 UNIQUE에 걸리지 않는다
    assert r1.matched is True and r2.matched is True and r1.diff == []
    stmt = select(Decision.status, func.count()).group_by(Decision.status)
    assert dict(session.execute(stmt).all()) == {"pending": 1, "replay": 2}
    assert DecisionCrud(session).real_decision(ASOF.isoformat(), "paper").id == real.id


def test_replay_with_different_budget_reports_diff(session):
    seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    svc = make(session)
    svc.decide_month_end(ASOF, "paper")
    r = svc.replay(ASOF, "stored", portfolio=Portfolio(300_000, 0))  # 예산이 작아 비싼 종목이 빠진다
    assert r.matched is False and r.diff


def test_trend_filter_switches_to_cash(session):
    seed_market(session, index_trend="down")
    cfg = seed_default_config(DecisionCrud(session), "2025-01-01")
    cfg.trend_filter = 1
    session.flush()
    d = make(session).decide_month_end(ASOF, "paper")
    assert d.cash_switch == 1 and DecisionCrud(session).picks(d.id) == []


def test_excluded_status_is_skipped(session):
    codes = seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    ids = DecisionCrud(session).instrument_ids()
    svc = make(session)
    first = svc.replay(ASOF, "none").picked[0]
    session.add(InstrumentStatus(instrument_id=ids[first], status="managed", starts_on="2025-05-01", ends_on=None))
    session.flush()
    d = svc.decide_month_end(ASOF, "paper")
    assert first not in DecisionCrud(session).picks(d.id)
    row = session.scalar(select(Score).where(Score.decision_id == d.id, Score.instrument_id == ids[first]))
    assert row.skip_reason == "status" and row.selected == 0
    assert len(codes) > 10


def test_index_rebalance_flag(session):
    seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    assert make(session, index_value=600_000).decide_month_end(ASOF, "paper").index_rebalance == 0  # 20%
    session.query(Score).delete()
    session.query(Decision).delete()
    assert make(session, index_value=0).decide_month_end(ASOF, "paper").index_rebalance == 1


def test_research_file_without_date_returns_none(session):
    seed_market(session, asof="2025-05-15")
    seed_default_config(DecisionCrud(session), "2025-01-01")
    r = make(session).replay(date(2025, 5, 15), "research_file")
    assert r.matched is None and r.compared_with == "research_file"


def test_replay_stored_falls_back_to_research_file(session):
    seed_market(session, asof="2025-05-15")
    seed_default_config(DecisionCrud(session), "2025-01-01")
    r = make(session).replay(date(2025, 5, 15), "stored")
    assert r.compared_with == "research_file"


def test_trend_filter_refuses_short_index_history(session):
    """지수 이력이 10개월이 안 되면 판단을 거부한다. 짧은 평균으로 조용히 판단하면 규칙이 바뀐 것과 같다.

    2026-09-23 실제로 지수 적재가 첫 페이지(두 달)에서 멈춰 3개월 평균으로 판단할 뻔했다.
    """
    seed_market(session, index_trend="down")
    cfg = seed_default_config(DecisionCrud(session), "2025-01-01")
    cfg.trend_filter = 1
    session.flush()
    session.query(IndexLevel).filter(IndexLevel.date < "2025-04-01").delete()
    with pytest.raises(DataNotReady, match="코스피 월말 종가"):
        make(session).decide_month_end(ASOF, "paper")


def test_trend_filter_refuses_a_gap_month(session):
    """개수가 10개여도 중간 달이 비면 거부한다. 봇이 한 달 넘게 꺼졌다 켜지면 실제로 생긴다."""
    seed_market(session, index_trend="down")
    cfg = seed_default_config(DecisionCrud(session), "2025-01-01")
    cfg.trend_filter = 1
    session.flush()
    session.query(IndexLevel).filter(IndexLevel.date.like("2025-02%")).delete()
    with pytest.raises(DataNotReady, match="2025-02"):
        make(session).decide_month_end(ASOF, "paper")

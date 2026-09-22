"""규칙 재점검 (QBOT-UC-001 UC-H3). 설정은 사람이 승인해야 바뀐다."""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from app.core.clock import KST, FrozenClock
from app.core.errors import DataNotReady, Precondition
from app.domains.decision.crud import DecisionCrud
from app.domains.decision.service import DecisionService, Portfolio, factor_spread, seed_default_config
from app.domains.marketdata.models import TradingCalendar
from app.domains.marketdata.service import MarketDataService
from tests.domains.decision.helpers import seed_market

ASOF = date(2025, 5, 30)


def make(session, month_ends: int = 12):
    seed_market(session, n=30, asof=ASOF.isoformat())
    dates = sorted({d for (d,) in session.execute(text("select distinct trade_date from daily_bar"))})
    ends = []
    for i, d in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        is_end = nxt is None or nxt[:7] != d[:7]
        session.add(TradingCalendar(date=d, is_trading_day=1, is_month_end=int(is_end)))
        if is_end:
            ends.append(d)
    seed_default_config(DecisionCrud(session), "2019-01-01")
    session.commit()
    md = MarketDataService(session, clock=FrozenClock(datetime(2025, 5, 30, 18, 50, tzinfo=KST)))
    svc = DecisionService(session, md, lambda: Portfolio(1_000_000, 0), "test")
    return svc, ends


def test_factor_spread_is_top_decile_minus_bottom_decile():
    values = pd.Series(range(100), index=[f"{i:06d}" for i in range(100)], dtype="float64")
    forward = values / 100  # 값이 클수록 다음 달 수익률이 높다
    assert factor_spread(values, forward, 1) == pytest.approx(0.9, abs=0.02)
    assert factor_spread(values, forward, -1) == pytest.approx(-0.9, abs=0.02)  # 방향이 반대면 부호도 반대
    assert factor_spread(values.head(10), forward, 1) is None  # 표본이 적으면 판단하지 않는다


def test_review_scores_every_candidate_and_stores_a_record(session):
    svc, ends = make(session)
    row = svc.review_rules(ASOF, months=12)
    session.commit()
    results = json.loads(row.results_json)
    assert set(results) == {"EP", "SP", "ROE", "OPG", "OPG_Q", "VOL60", "TURN20"}
    assert row.decision is None  # 사람이 승인하기 전에는 아무것도 바뀌지 않는다
    assert all(r["in_use"] for r in results.values())  # 기본 설정은 7개를 다 쓴다
    assert "review_approve" in svc.describe_review(row)


def test_review_needs_enough_months(session):
    svc, _ = make(session)
    session.query(TradingCalendar).filter(TradingCalendar.date > "2025-01-01").delete()
    session.execute(text("update trading_calendar set is_month_end = 0 where date < '2024-12-01'"))
    session.commit()
    with pytest.raises(DataNotReady):
        svc.review_rules(ASOF, months=36)


def test_approve_creates_a_new_config_for_the_next_day(session):
    svc, _ = make(session)
    row = svc.review_rules(ASOF, months=12)
    row.adopted_json = json.dumps(["EP", "ROE"])  # 채택 결과를 흉내
    session.commit()
    cfg = svc.approve_review(row.id, command_id=7)
    session.commit()
    assert json.loads(cfg.factors_json) == {"EP": 1, "ROE": 1}
    assert cfg.effective_from == "2025-05-31" and cfg.source_review_id == row.id
    assert cfg.approved_by_command_id == 7 and row.decision == "approved"
    assert DecisionCrud(session).config_effective("2025-05-31").id == cfg.id


def test_approve_twice_is_refused(session):
    svc, _ = make(session)
    row = svc.review_rules(ASOF, months=12)
    row.adopted_json = json.dumps(["EP"])
    session.commit()
    svc.approve_review(row.id)
    with pytest.raises(Precondition):
        svc.approve_review(row.id)


def test_empty_adoption_is_refused(session):
    """지표를 다 버리면 점수를 낼 수 없다. 빈 설정을 만들 바에는 그대로 두는 게 낫다."""
    svc, _ = make(session)
    row = svc.review_rules(ASOF, months=12)
    row.adopted_json = json.dumps([])
    session.commit()
    with pytest.raises(Precondition):
        svc.approve_review(row.id)

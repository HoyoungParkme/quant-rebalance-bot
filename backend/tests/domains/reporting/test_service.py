"""보고 서비스 — 일일 요약과 월간 보고 (QBOT-UC-001 UC-A6)."""

from __future__ import annotations

import json
from datetime import date

from app.domains.reporting.service import ReportingService
from app.domains.risk.models import Valuation


def val(session, day, total, cash=0, flow=0, drawdown=0.0, pnl=0.0):
    session.add(
        Valuation(
            date=day,
            mode="paper",
            cash=cash,
            stock_value=total - cash,
            index_value=0,
            total=total,
            external_flow=flow,
            drawdown=drawdown,
            pnl_vs_principal=pnl,
        )
    )
    session.commit()


def test_daily_summary_without_a_record(session):
    svc = ReportingService(session)
    assert "기록이 없다" in svc.daily_summary(date(2026, 9, 22))


def test_daily_summary_shows_the_split_and_the_extremes(session):
    val(session, "2026-09-22", 1_000_000, cash=300_000, drawdown=-0.05, pnl=0.12)
    svc = ReportingService(
        session,
        positions_fn=lambda: [
            {"code": "000001", "pnl_pct": 0.2},
            {"code": "000002", "pnl_pct": -0.1},
        ],
    )
    out = svc.daily_summary(date(2026, 9, 22))
    assert "1,000,000원" in out and "현금 300,000" in out
    assert "-5.0%" in out and "+12.0%" in out
    assert "최고 000001 +20.0%" in out and "최저 000002 -10.0%" in out


def test_monthly_starts_from_last_months_close(session):
    """시작값은 지난달 마지막 평가액이다. 이 달 첫 기록을 쓰면 첫날 등락이 통째로 빠진다."""
    val(session, "2026-07-31", 900_000)
    val(session, "2026-08-03", 1_000_000)
    val(session, "2026-08-31", 1_100_000)
    svc = ReportingService(session)
    m = json.loads(svc.monthly(2026, 8, "paper").metrics_json)
    assert m["start_total"] == 900_000 and abs(m["ret"] - (1_100_000 / 900_000 - 1)) < 1e-9


def test_monthly_compares_with_kospi_and_removes_deposits(session):
    val(session, "2026-08-03", 1_000_000)
    val(session, "2026-08-14", 1_500_000, flow=400_000, drawdown=-0.02)
    val(session, "2026-08-31", 1_600_000, drawdown=-0.08)
    svc = ReportingService(
        session,
        index_closes=lambda name, frm, to: {"2026-08-03": 2000.0, "2026-08-31": 2100.0},
        trade_stats=lambda frm, to: (12, 3_400),
    )
    row = svc.monthly(2026, 8, "paper")
    m = json.loads(row.metrics_json)
    # 140만원(원금 100 + 입금 40)이 160만원이 됐다 → +14.3%. 입금을 수익으로 세지 않는다
    assert m["ret"] == round(1_600_000 / 1_400_000 - 1, 10) or abs(m["ret"] - 0.142857) < 1e-5
    assert abs(m["kospi_ret"] - 0.05) < 1e-9 and m["gap"] > 0
    assert m["max_drawdown"] == -0.08 and m["trades"] == 12 and m["fees"] == 3_400
    assert "코스피" in svc.describe_monthly(row)


def test_monthly_without_records_says_so_and_does_not_crash(session):
    svc = ReportingService(session)
    row = svc.monthly(2026, 1, "paper")
    assert "기록이 없다" in svc.describe_monthly(row)


def test_monthly_is_updated_not_duplicated(session):
    val(session, "2026-08-03", 1_000_000)
    svc = ReportingService(session)
    first = svc.monthly(2026, 8, "paper")
    val(session, "2026-08-31", 1_200_000)
    second = svc.monthly(2026, 8, "paper")
    assert first.id == second.id and json.loads(second.metrics_json)["end_total"] == 1_200_000

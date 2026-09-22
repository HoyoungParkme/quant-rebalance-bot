"""실제 API 연기 테스트 (QBOT-CODE-001 C1). QBOT_LIVE_TESTS=1 일 때만 돈다. 돈이 움직이는 호출은 없다."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("QBOT_LIVE_TESTS") != "1", reason="실제 API 호출은 QBOT_LIVE_TESTS=1")


@pytest.fixture(scope="module")
def app():
    from app.main import build

    return build()


def test_kis_daily_bars_and_index(app):
    to = date.today()
    frm = to - timedelta(days=20)
    bars = app.md.broker.daily_bars("005930", frm.isoformat(), to.isoformat())
    assert 8 <= len(bars) <= 15 and all(b.close > 0 for b in bars)
    idx = app.md.broker.index_closes("KOSPI", frm.isoformat(), to.isoformat())
    assert len(idx) >= 8


def test_kis_instrument_list(app):
    rows = app.md.broker.instrument_list()
    by = {r.code: r for r in rows}
    assert len(rows) > 3000 and by["005930"].kind == "common" and by["069500"].kind == "etf"
    assert by["005930"].shares_outstanding > 5_000_000_000


def test_kis_calendar_live_key_only(app):
    if "openapivts" in app.settings.query_credentials()[0]:
        pytest.skip("휴장일 조회는 실전 키에서만")
    to = date.today() + timedelta(days=30)
    days = app.md.broker.calendar(date.today().isoformat(), to.isoformat())
    assert len(days) >= 20 and any(d.is_trading_day for d in days) and any(not d.is_trading_day for d in days)


def test_dart_filings_and_financials(app):
    y = date.today() - timedelta(days=7)
    fil = app.md.filings.list_filings(y.isoformat(), date.today().isoformat())
    assert isinstance(fil, list)
    rows = app.md.filings.financials("00126380", "2025", "11013")  # 삼성전자 2025 1분기
    assert rows is not None and rows.values["operating_income"] > 0 and rows.cumulative["operating_income"] > 0


def test_telegram_send(app):
    assert app.ops.notifier.send("[모의] C2 연기 테스트: 알림 경로 확인") is True

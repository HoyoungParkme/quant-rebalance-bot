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


def test_kis_balance_and_orders_today(app):
    """주문용 접속(모드 키)이 열리는지. 돈은 움직이지 않는다."""
    b = app.trading.balance()
    assert b.cash >= 0 and b.total_equity >= b.cash - 1
    assert isinstance(app.trading.broker.orders_today(), list)


@pytest.mark.skipif(os.environ.get("QBOT_LIVE_ORDER") != "1", reason="실제 주문은 QBOT_LIVE_ORDER=1")
def test_paper_order_roundtrip(app):
    """모의 계좌에 1주 매수 → 체결 확인 → 미체결이면 취소, 체결이면 1주 매도 (QBOT-CODE-001 D1).

    DB를 건드리지 않고 어댑터만 쓴다. 실행기의 상태 전이는 단위 테스트가 본다.
    """
    from app.core.errors import BrokerRejected
    from app.domains.trading.ports import OrderRequest

    assert app.settings.mode == "paper", "실전 계좌로는 이 테스트를 돌리지 않는다"
    code = "005930"
    try:
        no = app.trading.broker.place_order(OrderRequest(code, "buy", 1, 0))
    except BrokerRejected as e:
        # 여기까지 왔다는 건 거래 ID·계좌·해시키·본문이 다 통과했다는 뜻이다. 남은 건 장 시간뿐
        if any(k in str(e) for k in ("장종료", "장시작", "주문시간", "휴장")):
            pytest.skip(f"장 시간이 아니다(요청 자체는 통과): {e}")
        raise
    assert no
    st = app.trading.broker.order_status(no)
    assert st is not None and st.code == code and st.side == "buy" and st.qty == 1
    if st.filled_qty >= 1:
        app.trading.broker.place_order(OrderRequest(code, "sell", 1, 0))
    else:
        app.trading.broker.cancel_order(no)

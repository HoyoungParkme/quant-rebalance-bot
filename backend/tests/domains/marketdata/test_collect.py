"""수집·적재 (QBOT-MS-001 collect_daily 테스트 관점, UC-A1, UC-H5)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from app.core.clock import FrozenClock
from app.domains.marketdata.models import (
    DailyBar,
    Filing,
    FinancialSnapshot,
    IndexLevel,
    Instrument,
    InstrumentStatus,
)
from app.domains.marketdata.ports import CalendarDay, FilingInfo, FinancialRows, InstrumentInfo
from app.domains.marketdata.service import MarketDataService
from tests.domains.marketdata.fakes import FakeBroker, FakeFilings, bar

CLOCK = FrozenClock(datetime(2025, 5, 30, 18, 30))


def svc(session, broker, filings, held=None):
    return MarketDataService(session, broker=broker, filings=filings, clock=CLOCK, held_codes_fn=lambda: held or set())


def base_broker(days=("2025-05-28", "2025-05-29", "2025-05-30")):
    b = FakeBroker()
    b.instruments = [
        InstrumentInfo("000001", "가", "KOSPI", "common", "2000-01-01", 1000, frozenset()),
        InstrumentInfo("000002", "나", "KOSDAQ", "common", "2010-01-01", 2000, frozenset({"managed"})),
    ] + [InstrumentInfo(f"9{k:05d}", "x", "KOSPI", "other", None, None, frozenset()) for k in range(1000)]
    b.bars = {c: [bar(d, 1000 + i) for i, d in enumerate(days)] for c in ("000001", "000002")}
    b.days = [CalendarDay(d, True) for d in days] + [CalendarDay("2025-05-31", False)]
    b.index = {"KOSPI": {d: 2500.0 for d in days}, "KOSPI200": {}, "KOSDAQ": {}}
    return b


def test_collect_daily_adds_instruments_bars_statuses_index(session):
    b = base_broker()
    res = svc(session, b, FakeFilings()).collect_daily(date(2025, 5, 30))
    assert not res.skipped and res.instruments_added == 1002 and res.bars_ok == 6 and res.bars_failed == []
    assert session.scalar(select(func.count()).select_from(DailyBar)) == 6
    st = session.scalars(select(InstrumentStatus)).all()
    assert len(st) == 1 and st[0].status == "managed" and st[0].starts_on == "2025-05-30" and st[0].ends_on is None
    # 두 번 돌려도 중복 없음
    res2 = svc(session, b, FakeFilings()).collect_daily(date(2025, 5, 30))
    assert res2.bars_ok == 0 and session.scalar(select(func.count()).select_from(DailyBar)) == 6


def test_status_closes_and_delisting(session):
    b = base_broker()
    s = svc(session, b, FakeFilings())
    s.collect_daily(date(2025, 5, 30))
    b.instruments = [InstrumentInfo("000001", "가", "KOSPI", "common", "2000-01-01", 1000, frozenset({"halted"}))] + [
        i for i in b.instruments if i.code.startswith("9")
    ]
    b.days.append(CalendarDay("2025-06-02", True))
    b.bars["000001"].append(bar("2025-06-02", 1003))
    s.collect_daily(date(2025, 6, 2))
    two = session.scalar(select(Instrument).where(Instrument.code == "000002"))
    assert two.delisted_on == "2025-06-02"
    st = {(x.status, x.ends_on) for x in session.scalars(select(InstrumentStatus))}
    assert ("managed", "2025-06-01") in st and ("halted", None) in st


def test_series_bump_on_adjusted_prices(session):
    b = base_broker()
    s = svc(session, b, FakeFilings())
    s.collect_daily(date(2025, 5, 30))
    # 액면분할: 과거 종가가 절반으로 바뀜
    b.bars["000001"] = [bar(d, (1000 + i) // 2) for i, d in enumerate(("2025-05-28", "2025-05-29", "2025-05-30"))]
    b.bars["000001"].append(bar("2025-06-02", 502))
    b.days.append(CalendarDay("2025-06-02", True))
    res = s.collect_daily(date(2025, 6, 2))
    assert res.series_bumped == ["000001"]
    rows = session.scalars(select(DailyBar).join(Instrument).where(Instrument.code == "000001")).all()
    assert {r.series_no for r in rows} == {1, 2}
    assert all(r.close >= 1000 for r in rows if r.series_no == 1)  # 옛 판은 그대로 남는다
    assert any(r.trade_date == "2025-06-02" and r.series_no == 2 for r in rows)


def test_filings_and_quarter_derivation(session):
    b = base_broker()
    f = FakeFilings()
    f.filings = [
        FilingInfo("R3", "000001", "C1", "quarter", "11014", "2024", "2025-05-29", "분기보고서 (2024.09)"),
        FilingInfo("R4", "000001", "C1", "annual", "11011", "2024", "2025-05-30", "사업보고서 (2024.12)"),
        FilingInfo("R5", "000001", "C1", "other", None, None, "2025-05-30", "감사의견 의견거절 공시"),
    ]
    f.fin[("C1", "2024", "11014")] = FinancialRows("R3", 1, {"operating_income": 30}, {"operating_income": 90})
    f.fin[("C1", "2024", "11011")] = FinancialRows(
        "R4", 1, {"revenue": 500, "operating_income": 120, "net_income": 80, "equity": 1000}, {}
    )
    s = svc(session, b, f, held={"000001"})
    s.collect_daily(date(2025, 5, 29))
    res = s.collect_daily(date(2025, 5, 30))
    assert res.filings_added == 2 and res.alerts and "의견거절" in res.alerts[0]
    snaps = session.scalars(select(FinancialSnapshot)).all()
    kinds = {(x.period_end, x.period_kind): x.operating_income for x in snaps}
    assert kinds[("2024-09-30", "quarter")] == 30 and kinds[("2024-09-30", "cum3q")] == 90
    assert kinds[("2024-12-31", "annual")] == 120 and kinds[("2024-12-31", "quarter")] == 30  # 120 - 90


def test_filing_numbers_must_belong_to_that_filing(session):
    b = base_broker()
    f = FakeFilings()
    f.filings = [FilingInfo("R1", "000001", "C1", "annual", "11011", "2024", "2025-05-30", "사업보고서 (2024.12)")]
    f.fin[("C1", "2024", "11011")] = FinancialRows("R9", 1, {"operating_income": 1}, {})  # 다른 공시(정정본)의 숫자
    svc(session, b, f).collect_daily(date(2025, 5, 30))
    assert session.scalar(select(func.count()).select_from(FinancialSnapshot)) == 0


def test_non_trading_day_is_skipped(session):
    b = base_broker()
    assert svc(session, b, FakeFilings()).collect_daily(date(2025, 5, 31)).skipped is True


def test_backfill_resumes_without_duplicates(session, tmp_path):
    b = base_broker()
    s = svc(session, b, FakeFilings())
    (tmp_path / "999999.csv").write_text("date,open,high,low,close,volume,amount\n2025-05-28,10,10,10,10,5,50\n")
    out1 = s.backfill(date(2025, 5, 28), ["calendar", "status", "index", "bars"], tmp_path)
    out2 = s.backfill(date(2025, 5, 28), ["bars"], tmp_path)
    assert out1["bars"] == 6 and out1["research_bars"] == 1 and out2["bars"] == 0 and out2["research_bars"] == 0
    dl = session.scalar(select(Instrument).where(Instrument.code == "999999"))
    assert dl.delisted_on is not None


def test_filing_without_numbers_is_retried_next_run(session):
    b = base_broker()
    f = FakeFilings()
    f.filings = [FilingInfo("R1", "000001", "C1", "annual", "11011", "2024", "2025-05-29", "사업보고서 (2024.12)")]
    s = svc(session, b, f)
    s.collect_daily(date(2025, 5, 29))
    assert session.scalar(select(func.count()).select_from(FinancialSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(Filing)) == 0
    f.fin[("C1", "2024", "11011")] = FinancialRows("R1", 1, {"operating_income": 7}, {})  # 다음날 숫자가 반영됨
    res = s.collect_daily(date(2025, 5, 30))
    assert res.filings_added == 1 and session.scalar(select(func.count()).select_from(FinancialSnapshot)) == 1


def test_gap_after_outage_is_filled(session):
    b = base_broker(days=("2025-05-02", "2025-05-05"))
    s = svc(session, b, FakeFilings())
    s.collect_daily(date(2025, 5, 5))
    for d in ("2025-05-07", "2025-05-08", "2025-05-30"):  # 3주 넘게 멈춤
        b.days.append(CalendarDay(d, True))
        for c in ("000001", "000002"):
            b.bars[c].append(bar(d, 1100))
    res = s.collect_daily(date(2025, 5, 30))
    assert res.bars_ok == 6
    dates = set(session.scalars(select(DailyBar.trade_date)))
    assert {"2025-05-07", "2025-05-08", "2025-05-30"} <= dates


def test_index_gap_after_long_outage_is_filled(session):
    """지수는 마지막으로 받은 날부터 채운다. 30일만 받으면 한 달 넘게 꺼졌던 뒤 월말이 빠진다.

    빠진 월말은 추세 필터가 "연속 10개월"을 못 채워 판단을 거부하게 만든다.
    """
    b = base_broker(days=("2025-03-03",))
    b.index["KOSPI200"]["2025-03-03"] = b.index["KOSDAQ"]["2025-03-03"] = 2500.0
    s = svc(session, b, FakeFilings())
    s.collect_daily(date(2025, 3, 3))
    for d in ("2025-03-31", "2025-04-30", "2025-05-30"):  # 3월 초부터 5월 말까지 꺼져 있었다
        b.days.append(CalendarDay(d, True))
        for name in ("KOSPI", "KOSPI200", "KOSDAQ"):
            b.index[name][d] = 2600.0
    s.collect_daily(date(2025, 5, 30))
    got = set(session.scalars(select(IndexLevel.date).where(IndexLevel.index_name == "KOSPI")))
    assert {"2025-03-31", "2025-04-30", "2025-05-30"} <= got


def test_bad_master_does_not_mass_delist(session):
    import pytest

    from app.core.errors import BrokerUnavailable

    b = base_broker()
    s = svc(session, b, FakeFilings())
    s.collect_daily(date(2025, 5, 30))
    b.instruments = []
    with pytest.raises(BrokerUnavailable):
        s.collect_daily(date(2025, 5, 30))
    assert all(i.delisted_on is None for i in session.scalars(select(Instrument)))


def test_intraday_bar_is_settled_not_treated_as_a_split(session):
    """장중에 받아 둔 오늘 봉과 확정 종가가 다른 것은 수정주가가 아니다.

    판을 올리면 3,500종목의 이력을 통째로 다시 받는다(2026-09-22에 실제로 그렇게 됐다).
    """
    b = base_broker()
    m = svc(session, b, FakeFilings())
    m.collect_daily(date(2025, 5, 30))
    before = session.scalar(select(func.count()).select_from(DailyBar))

    # 장 마감 뒤 확정 종가가 장중 값과 다르다
    b.bars["000001"] = [bar("2025-05-30", 9999)]
    res = m.collect_daily(date(2025, 5, 30))
    rows = session.scalars(select(DailyBar).where(DailyBar.trade_date == "2025-05-30")).all()
    assert res.series_bumped == [] and {r.series_no for r in rows} == {1}
    assert session.scalar(select(func.count()).select_from(DailyBar)) == before  # 행이 늘지 않았다
    inst = session.scalars(select(Instrument).where(Instrument.code == "000001")).one()
    got = next(r for r in rows if r.instrument_id == inst.id)
    assert got.close == 9999  # 잠정값을 확정값으로 고쳤다


def test_a_real_adjustment_still_bumps(session):
    """끝난 날의 값이 바뀐 것은 진짜 수정주가다. 그때는 판을 올려 다시 받는다."""
    b = base_broker()
    m = svc(session, b, FakeFilings())
    m.collect_daily(date(2025, 5, 30))
    # 다음 거래일. 지난 날들의 값이 절반으로 바뀌었다(액면분할)
    b.days.append(CalendarDay("2025-06-02", True))
    b.bars["000001"] = [bar("2025-05-29", 500), bar("2025-05-30", 501), bar("2025-06-02", 502)]
    res = m.collect_daily(date(2025, 6, 2))
    assert "000001" in res.series_bumped
    assert 2 in {r.series_no for r in session.scalars(select(DailyBar)).all()}


def test_evening_bar_that_changes_by_next_day_is_settled_not_a_split(session):
    """거래일 당일 저녁에 받은 봉도 잠정값이다. 다음 날 값이 달라도 수정주가가 아니다.

    2026-09-23: 전날 18:58에 받은 종가가 다음 날 받은 같은 날 종가와 802종목에서 달라
    802종목 이력을 통째로 다시 받았다. 당일 봉은 장 마감 뒤에도 저녁까지 바뀐다.
    """
    b = base_broker()
    evening = MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 5, 30, 18, 58)),
        held_codes_fn=lambda: set(),
    )
    evening.collect_daily(date(2025, 5, 30))
    # 다음 거래일: 5/30의 확정 종가는 저녁에 받은 값(1002)과 다르다. 그 전 날들은 그대로다
    b.days.append(CalendarDay("2025-06-02", True))
    b.bars["000001"] = [
        bar("2025-05-28", 1000),
        bar("2025-05-29", 1001),
        bar("2025-05-30", 990),
        bar("2025-06-02", 995),
    ]
    next_day = MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 6, 2, 18, 30)),
        held_codes_fn=lambda: set(),
    )
    res = next_day.collect_daily(date(2025, 6, 2))
    assert res.series_bumped == []
    inst = session.scalars(select(Instrument).where(Instrument.code == "000001")).one()
    row = session.scalars(
        select(DailyBar).where(DailyBar.instrument_id == inst.id, DailyBar.trade_date == "2025-05-30")
    ).one()
    assert row.close == 990 and row.series_no == 1 and row.collected_at.startswith("2025-06-02")  # 이제 확정


def test_finalize_day_rewrites_the_provisional_month_end_bar(session):
    """월말 판단 전 아침에 그 봉을 확정값으로 다시 받는다."""
    b = base_broker()
    evening = MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 5, 30, 18, 30)),
        held_codes_fn=lambda: set(),
    )
    evening.collect_daily(date(2025, 5, 30))
    b.bars["000001"][-1] = bar("2025-05-30", 777)
    b.index["KOSPI"]["2025-05-30"] = 2555.0
    morning = MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 6, 2, 7, 40)),
        held_codes_fn=lambda: set(),
    )
    assert morning.finalize_day(date(2025, 5, 30)) == []
    inst = session.scalars(select(Instrument).where(Instrument.code == "000001")).one()
    row = session.scalars(
        select(DailyBar).where(DailyBar.instrument_id == inst.id, DailyBar.trade_date == "2025-05-30")
    ).one()
    assert row.close == 777 and row.series_no == 1
    kospi = session.scalars(
        select(IndexLevel).where(IndexLevel.index_name == "KOSPI", IndexLevel.date == "2025-05-30")
    ).one()
    assert kospi.close == 2555.0


def test_backfill_resume_still_detects_a_split_after_a_provisional_evening(session, tmp_path):
    """이어받는 적재도 마지막 확정 봉부터 받는다. 잠정 봉부터 받으면 비교 기준일이 빠져 분할을 놓친다."""
    b = base_broker()
    MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 5, 30, 18, 30)),
        held_codes_fn=lambda: set(),
    ).collect_daily(date(2025, 5, 30))
    b.days.append(CalendarDay("2025-06-02", True))
    b.bars["000001"] = [bar("2025-05-28", 500), bar("2025-05-29", 501), bar("2025-05-30", 502), bar("2025-06-02", 503)]
    m = MarketDataService(
        session,
        broker=b,
        filings=FakeFilings(),
        clock=FrozenClock(datetime(2025, 6, 2, 18, 30)),
        held_codes_fn=lambda: set(),
    )
    m.backfill(date(2025, 5, 1), ["bars"])
    inst = session.scalars(select(Instrument).where(Instrument.code == "000001")).one()
    assert 2 in set(session.scalars(select(DailyBar.series_no).where(DailyBar.instrument_id == inst.id)))

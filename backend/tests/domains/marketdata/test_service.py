"""MarketDataService.financials / bars (QBOT-MS-001 테스트 관점)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.pit import PointInTime
from app.domains.marketdata.models import DailyBar, Filing, FinancialSnapshot, Instrument, InstrumentStatus
from app.domains.marketdata.service import MarketDataService

FIX = Path(__file__).resolve().parents[2] / "fixtures"


def inst(s, code, shares=1000):
    i = Instrument(code=code, name=code, market="KOSPI", kind="common", shares_outstanding=shares)
    s.add(i)
    s.flush()
    return i


def filing(s, i, rcept_no, rcept_date, kind="annual", corrects=None):
    f = Filing(
        rcept_no=rcept_no,
        instrument_id=i.id,
        report_kind=kind,
        rcept_date=rcept_date,
        corrects_rcept_no=corrects,
        title="t",
        collected_at="2026-01-01T00:00:00",
    )
    s.add(f)
    s.flush()
    return f


def snap(s, f, period_end, kind="annual", op=100, ni=50, eq=1000, rev=500, cons=1):
    s.add(
        FinancialSnapshot(
            filing_id=f.id,
            instrument_id=f.instrument_id,
            period_end=period_end,
            period_kind=kind,
            consolidated=cons,
            revenue=rev,
            operating_income=op,
            net_income=ni,
            equity=eq,
        )
    )
    s.flush()


def test_same_day_filing_is_excluded(session):
    i = inst(session, "000001")
    snap(session, filing(session, i, "1", "2025-05-30"), "2024-12-31", op=999)
    snap(session, filing(session, i, "2", "2025-05-29"), "2024-12-31", op=111)
    fin = MarketDataService(session).financials(PointInTime(date(2025, 5, 30)))
    assert fin.loc["000001", "operating_income"] == 111


def test_correction_after_asof_is_ignored_but_before_is_used(session):
    i = inst(session, "000002")
    snap(session, filing(session, i, "10", "2025-03-20"), "2024-12-31", op=100)
    snap(session, filing(session, i, "11", "2025-06-10", corrects="10"), "2024-12-31", op=200)
    svc = MarketDataService(session)
    assert svc.financials(PointInTime(date(2025, 5, 30))).loc["000002", "operating_income"] == 100
    assert svc.financials(PointInTime(date(2025, 6, 30))).loc["000002", "operating_income"] == 200


def test_consolidated_preferred_and_old_excluded(session):
    i = inst(session, "000003")
    f = filing(session, i, "20", "2025-03-20")
    snap(session, f, "2024-12-31", op=1, cons=0)
    snap(session, f, "2024-12-31", op=2, cons=1)
    j = inst(session, "000004")
    snap(session, filing(session, j, "21", "2023-03-20"), "2022-12-31", op=5)  # 500일 초과
    fin = MarketDataService(session).financials(PointInTime(date(2025, 5, 30)))
    assert fin.loc["000003", "operating_income"] == 2
    assert "000004" not in fin.index


def test_quarter_and_prev_year_quarter_and_prev_annual(session):
    i = inst(session, "000005", shares=77)
    snap(session, filing(session, i, "30", "2024-03-20"), "2023-12-31", op=80)
    snap(session, filing(session, i, "31", "2025-03-20"), "2024-12-31", op=120)
    snap(session, filing(session, i, "32", "2024-05-10", "quarter"), "2024-03-31", kind="quarter", op=10)
    snap(session, filing(session, i, "33", "2025-05-10", "quarter"), "2025-03-31", kind="quarter", op=15)
    fin = MarketDataService(session).financials(PointInTime(date(2025, 5, 30)))
    r = fin.loc["000005"]
    assert (r.operating_income, r.op_annual_prev, r.op_q, r.op_q_prev, r.shares) == (120, 80, 15, 10, 77)


def test_prev_year_must_be_exact_and_quarters_obey_age_rule(session):
    i = inst(session, "000008")
    snap(session, filing(session, i, "40", "2023-03-20"), "2022-12-31", op=80)  # 2023년이 빠짐
    snap(session, filing(session, i, "41", "2025-03-20"), "2024-12-31", op=120)
    snap(session, filing(session, i, "42", "2021-05-10", "quarter"), "2021-03-31", kind="quarter", op=7)
    fin = MarketDataService(session).financials(PointInTime(date(2025, 5, 30)))
    r = fin.loc["000008"]
    assert np.isnan(r.op_annual_prev)  # 2년 전 값을 쓰지 않는다
    assert np.isnan(r.op_q)  # 500일 넘은 분기는 버린다


def test_leap_day_prev_year():
    from app.domains.marketdata.service import _prev_year_period_end

    assert _prev_year_period_end("2024-02-29") == "2023-02-28"
    assert _prev_year_period_end("2025-03-31") == "2024-03-31"


def test_bar_amount_null_stays_nan(session):
    i = inst(session, "000009")
    session.add(
        DailyBar(
            instrument_id=i.id,
            trade_date="2025-05-30",
            series_no=1,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            amount=None,
            traded=1,
            collected_at="2025-05-30T18:30:00",
        )
    )
    session.flush()
    amt = MarketDataService(session).bars(PointInTime(date(2025, 5, 30)), 1)["amount"]
    assert np.isnan(amt.loc["2025-05-30", "000009"])


def test_no_date_argument_accepted():
    import inspect

    for name in ("financials", "bars", "statuses_on"):
        params = list(inspect.signature(getattr(MarketDataService, name)).parameters.values())
        assert params[1].annotation in ("PointInTime", PointInTime)


def test_bars_use_series_as_of_and_lookback(session):
    i = inst(session, "000006")
    for n, d in enumerate(["2025-05-26", "2025-05-27", "2025-05-28", "2025-05-29", "2025-05-30"], 1):
        session.add(
            DailyBar(
                instrument_id=i.id,
                trade_date=d,
                series_no=1,
                open=n,
                high=n,
                low=n,
                close=n * 10,
                volume=1,
                amount=1,
                traded=1,
                collected_at=d + "T18:30:00",
            )
        )
    # 6월에 액면분할: 5월 일봉이 새 판(2)으로 다시 들어옴
    for n, d in enumerate(["2025-05-26", "2025-05-27", "2025-05-28", "2025-05-29", "2025-05-30"], 1):
        session.add(
            DailyBar(
                instrument_id=i.id,
                trade_date=d,
                series_no=2,
                open=n,
                high=n,
                low=n,
                close=n,
                volume=1,
                amount=1,
                traded=1,
                collected_at="2025-06-15T18:30:00",
            )
        )
    session.flush()
    svc = MarketDataService(session)
    old = svc.bars(PointInTime(date(2025, 5, 30)), 3)["close"]
    assert list(old.index) == ["2025-05-28", "2025-05-29", "2025-05-30"]
    assert old.loc["2025-05-30", "000006"] == 50  # 그 시점엔 판 1
    new = svc.bars(PointInTime(date(2025, 6, 30)), 3)["close"]
    assert new.loc["2025-05-30", "000006"] == 5  # 6월 재현은 판 2


def test_statuses_on(session):
    i = inst(session, "000007")
    session.add(InstrumentStatus(instrument_id=i.id, status="managed", starts_on="2025-05-01", ends_on="2025-05-29"))
    session.add(InstrumentStatus(instrument_id=i.id, status="halted", starts_on="2025-05-30", ends_on=None))
    session.flush()
    svc = MarketDataService(session)
    assert svc.statuses_on(PointInTime(date(2025, 5, 15))) == {"000007": {"managed"}}
    assert svc.statuses_on(PointInTime(date(2025, 5, 30))) == {"000007": {"halted"}}


def test_matches_research_selection_logic(session):
    """검증 fs.csv 표본을 접수일=avail로 적재. 2025-05-30 기준 결과가 검증 스크립트의 선택 로직과 같다."""
    rows = list(csv.DictReader(open(FIX / "research_fs_sample.csv")))
    ids = {}
    for n, r in enumerate(rows):
        if r["code"] not in ids:
            ids[r["code"]] = inst(session, r["code"])
        f = filing(session, ids[r["code"]], f"R{n:04d}", r["avail"])
        snap(
            session,
            f,
            r["fy_end"],
            op=int(float(r["op"])),
            ni=int(float(r["ni_use"])),
            eq=int(float(r["equity_use"])),
            rev=int(float(r["rev"])),
            cons=1 if r["cons"] == "True" else 0,
        )
    asof = pd.Timestamp("2025-05-30")
    fs = pd.DataFrame(rows)
    fs["avail"] = pd.to_datetime(fs.avail)
    fs["fy_end"] = pd.to_datetime(fs.fy_end)
    fs = fs.sort_values(["code", "fy_end"])
    expect = fs[fs.avail <= asof].drop_duplicates("code", keep="last").set_index("code")  # 검증 스크립트와 같은 로직
    expect = expect[(asof - expect.fy_end).dt.days <= 500]
    got = MarketDataService(session).financials(PointInTime(asof.date()))
    assert set(got.index) == set(expect.index)
    for c in expect.index:
        assert got.loc[c, "operating_income"] == int(float(expect.loc[c, "op"]))
        assert got.loc[c, "net_income"] == int(float(expect.loc[c, "ni_use"]))
    assert not np.isnan(got.loc["005930", "op_annual_prev"])

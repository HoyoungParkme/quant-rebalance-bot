"""시장 데이터 서비스. 시점 고정 조회(QBOT-UC-001 UC-S1)와 수집(슬라이스 C1)이 여기 있다.

모든 조회는 PointInTime을 받는다. date를 받는 공개 조회 함수는 없다 (QBOT-PRD-001 R3).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.pit import PointInTime
from app.domains.marketdata.crud import MarketDataCrud

MAX_FIN_AGE_DAYS = 500
FIN_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "op_annual_prev",
    "op_q",
    "op_q_prev",
    "shares",
]


def _prev_year_period_end(period_end: str) -> str:
    """1년 전 같은 결산기 종료일. 윤년 2월 29일은 28일로."""
    d = date.fromisoformat(period_end)
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:
        return d.replace(year=d.year - 1, day=28).isoformat()


class MarketDataService:
    def __init__(self, session: Session) -> None:
        self.crud = MarketDataCrud(session)

    def financials(self, pit: PointInTime) -> pd.DataFrame:
        """QBOT-MS-001 MarketDataService.financials. 종목 코드 인덱스, FIN_COLUMNS."""
        rcept_until = pit.filings_before().isoformat()
        rows = self.crud.snapshots_until(rcept_until)
        if not rows:
            return pd.DataFrame(columns=FIN_COLUMNS)
        oldest = (pit.asof - timedelta(days=MAX_FIN_AGE_DAYS)).isoformat()
        rec = [
            {
                "instrument_id": s.instrument_id,
                "period_end": s.period_end,
                "period_kind": s.period_kind,
                "consolidated": s.consolidated,
                "rcept_date": f.rcept_date,
                "rcept_no": f.rcept_no,
                "revenue": s.revenue,
                "operating_income": s.operating_income,
                "net_income": s.net_income_owner if s.net_income_owner is not None else s.net_income,
                "equity": s.equity_owner if s.equity_owner is not None else s.equity,
            }
            for s, f in rows
        ]
        if not rec:
            return pd.DataFrame(columns=FIN_COLUMNS)
        df = pd.DataFrame(rec)
        # 종목·결산기간·기간종류마다: 접수일 늦은 것 → 같으면 공시번호 큰 것 → 연결 우선
        df = df.sort_values(["rcept_date", "rcept_no", "consolidated"]).drop_duplicates(
            ["instrument_id", "period_end", "period_kind"], keep="last"
        )
        annual = df[df.period_kind == "annual"].sort_values("period_end")
        quarter = df[df.period_kind == "quarter"].sort_values("period_end")
        latest_a = annual.groupby("instrument_id").tail(1).set_index("instrument_id")
        latest_a = latest_a[latest_a.period_end >= oldest]  # 최신 연간이 500일보다 오래되면 그 종목은 제외
        latest_q = quarter.groupby("instrument_id").tail(1).set_index("instrument_id")
        latest_q = latest_q[latest_q.period_end >= oldest]  # 분기도 같은 규칙
        # 직전 값은 "정확히 1년 전 같은 결산기". 빠져 있으면 NaN → 성장률 계산 불가로 제외된다
        aidx = annual.set_index(["instrument_id", "period_end"]).operating_income
        qidx = quarter.set_index(["instrument_id", "period_end"]).operating_income
        op_a_prev = pd.Series(
            [aidx.get((i, _prev_year_period_end(e)), np.nan) for i, e in latest_a.period_end.items()],
            index=latest_a.index,
        )
        op_q_prev = pd.Series(
            [qidx.get((i, _prev_year_period_end(e)), np.nan) for i, e in latest_q.period_end.items()],
            index=latest_q.index,
        )
        codes = {i.id: i.code for i in self.crud.instruments()}
        shares = {i.id: i.shares_outstanding for i in self.crud.instruments()}
        out = pd.DataFrame(index=latest_a.index)
        out["revenue"] = latest_a.revenue
        out["operating_income"] = latest_a.operating_income
        out["net_income"] = latest_a.net_income
        out["equity"] = latest_a.equity
        out["op_annual_prev"] = op_a_prev.reindex(out.index)
        out["op_q"] = latest_q.operating_income.reindex(out.index)
        out["op_q_prev"] = op_q_prev.reindex(out.index)
        out["shares"] = pd.Series(shares).reindex(out.index)
        out.index = out.index.map(codes)
        out.index.name = "code"
        return out[FIN_COLUMNS].astype(float)

    def bars(self, pit: PointInTime, lookback_days: int) -> dict[str, pd.DataFrame]:
        """QBOT-MS-001 MarketDataService.bars. {"close","open","volume","amount"} 각각 날짜 × 종목코드."""
        until = pit.bars_until().isoformat()
        dates = self.crud.trading_dates_until(until, lookback_days)
        if not dates:
            return {k: pd.DataFrame() for k in ("close", "open", "volume", "amount")}
        series = self.crud.series_asof(until)
        codes = {i.id: i.code for i in self.crud.instruments()}
        rows = [
            (b.trade_date, codes[b.instrument_id], b.open, b.close, b.volume, b.amount)  # NULL은 NaN
            for b in self.crud.bars_until(until, dates[0])
            if b.series_no == series.get(b.instrument_id, 1)
        ]
        df = pd.DataFrame(rows, columns=["date", "code", "open", "close", "volume", "amount"])
        num = ["open", "close", "volume", "amount"]
        df[num] = df[num].astype("float64")  # None → NaN
        piv = {
            k: df.pivot(index="date", columns="code", values=k).reindex(dates)
            for k in ("close", "open", "volume", "amount")
        }
        return piv

    def statuses_on(self, pit: PointInTime) -> dict[str, set[str]]:
        """기준일에 지정돼 있던 상태. {코드: {상태,...}}."""
        codes = {i.id: i.code for i in self.crud.instruments()}
        out: dict[str, set[str]] = {}
        for st in self.crud.statuses_on(pit.asof.isoformat()):
            out.setdefault(codes[st.instrument_id], set()).add(st.status)
        return out

    def index_month_ends(self, pit: PointInTime, index_name: str = "KOSPI", n: int = 10) -> list[float]:
        """기준일 이하 월말 종가 n개. 하락장 현금 전환 규칙(QBOT-PRD-001 R5)이 쓴다."""
        return self.crud.index_month_end_closes(index_name, pit.bars_until().isoformat(), n)

    def has_bars_on(self, pit: PointInTime) -> bool:
        return self.crud.has_bars_on(pit.asof.isoformat())

    def shares(self) -> pd.Series:
        return pd.Series({i.code: i.shares_outstanding for i in self.crud.instruments()}, dtype="float64")


def pit_of(asof: date) -> PointInTime:
    return PointInTime(asof)

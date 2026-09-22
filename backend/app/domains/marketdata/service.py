"""시장 데이터 서비스. 시점 고정 조회(QBOT-UC-001 UC-S1)와 수집(슬라이스 C1)이 여기 있다.

모든 조회는 PointInTime을 받는다. date를 받는 공개 조회 함수는 없다 (QBOT-PRD-001 R3).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BrokerUnavailable, Precondition, QbotError
from app.core.pit import PointInTime
from app.domains.marketdata.collect import Collector, CollectResult
from app.domains.marketdata.crud import MarketDataCrud
from app.domains.marketdata.models import Instrument

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
    def __init__(
        self, session: Session, broker=None, filings=None, clock: Clock | None = None, held_codes_fn=None
    ) -> None:
        self.crud = MarketDataCrud(session)
        self.broker, self.filings, self.clock = broker, filings, clock or Clock()
        self.held_codes_fn = held_codes_fn or (lambda: set())

    # ----- 수집·적재 (QBOT-MS-001 collect_daily, QBOT-API-001 backfill) -----
    def _collector(self) -> Collector:
        if self.broker is None or self.filings is None:
            raise Precondition("증권사·전자공시 접속이 설정되지 않았다")
        return Collector(self.crud, self.broker, self.filings, self.clock, self.held_codes_fn())

    def collect_daily(self, d: date) -> CollectResult:
        """거래일 저녁 수집. 실패한 종목은 결과에 담고 계속한다 (UC-A1)."""
        day = d.isoformat()
        col = self._collector()
        now_iso = self.clock.now().isoformat(timespec="seconds")
        res = CollectResult()
        if not self.crud.calendar_between(day, day):
            col.collect_calendar(day, (d + timedelta(days=45)).isoformat())
            if not self.crud.calendar_between(day, day):
                res.skipped = True
                return res
        try:
            res.instruments_added, res.status_changes = col.sync_instruments(day)
            for inst in self.crud.instruments():
                if inst.delisted_on is not None:
                    continue
                last = self.crud.last_bar(inst.id)
                # 마지막 저장일부터(그날 포함: 수정주가 판 감지). 오래 멈췄어도 빈 구간 없이 채운다
                frm = last.trade_date if last else (d - timedelta(days=10)).isoformat()
                try:
                    n, bumped = col.collect_bars(inst, frm, day, now_iso)
                except Exception as e:  # noqa: BLE001 - 한 종목 실패가 전체를 멈추면 안 된다
                    res.bars_failed.append(f"{inst.code}: {e}")
                    continue
                res.bars_ok += n
                if bumped:
                    res.series_bumped.append(inst.code)
            col.collect_index((d - timedelta(days=30)).isoformat(), day)
            # 공시는 전자공시 반영이 며칠 늦을 수 있어 2주를 다시 훑는다. 이미 넣은 것은 건너뛴다
            res.filings_added, res.alerts = col.collect_filings((d - timedelta(days=14)).isoformat(), day, now_iso)
        finally:
            self.crud.s.commit()  # 도중에 실패해도 받은 만큼 남긴다
        return res

    def backfill(self, frm: date, sources: list[str], research_dir: Path | None = None, progress=None) -> dict:
        """과거 적재. 끊기면 다시 실행해 이어 받는다 (UC-H5)."""
        col = self._collector()
        today = self.clock.today()
        now_iso = self.clock.now().isoformat(timespec="seconds")
        out: dict[str, int | list[str]] = {}
        if "calendar" in sources:
            out["calendar"] = col.collect_calendar(frm.isoformat(), (today + timedelta(days=45)).isoformat())
        if "status" in sources or "bars" in sources:
            out["instruments_added"], out["status_changes"] = col.sync_instruments(today.isoformat())
        if "index" in sources:
            out["index"] = col.collect_index(frm.isoformat(), today.isoformat())
        self.crud.s.commit()
        if "bars" in sources:
            ok, failed = 0, []
            live = [i for i in self.crud.instruments() if i.delisted_on is None]
            # 조회는 지연 시간이 길어 4개 스레드로 받고, DB 쓰기는 주 스레드에서 한다 (세션은 스레드 안전하지 않다)
            with ThreadPoolExecutor(max_workers=4) as pool:
                for k in range(0, len(live), 40):
                    batch = live[k : k + 40]
                    # 스레드에는 ORM 객체가 아니라 문자열만 넘긴다. 이어받기: 이미 있는 종목은 마지막 저장일부터
                    jobs = []
                    for i in batch:
                        last = self.crud.last_bar(i.id)
                        f0 = max(frm.isoformat(), last.trade_date) if last else frm.isoformat()
                        jobs.append((i, pool.submit(self.broker.daily_bars, i.code, f0, today.isoformat())))
                    for inst, fut in jobs:
                        try:
                            n, _ = col.store_bars(inst, fut.result(), today.isoformat(), now_iso)
                            ok += n
                        except Exception as e:  # noqa: BLE001
                            failed.append(f"{inst.code}: {e}")
                    self.crud.s.commit()  # 끊겨도 받은 만큼 남는다
                    if progress:
                        progress(f"일봉 {min(k + 40, len(live))}/{len(live)}종목, {ok}행")
            out["bars"], out["bars_failed"] = ok, failed
            if research_dir:
                out["research_bars"] = col.load_research_prices(research_dir, now_iso)
            self.crud.s.commit()
        if "filings" in sources:
            n_total = 0
            cur = frm
            while cur <= today:
                nxt = min(cur + timedelta(days=30), today)
                try:
                    n, _ = col.collect_filings(cur.isoformat(), nxt.isoformat(), now_iso)
                except BrokerUnavailable as e:  # 전자공시 일일 한도. 내일 다시 실행하면 이어진다
                    out["filings_stopped"] = f"{cur}: {e}"
                    break
                n_total += n
                self.crud.s.commit()
                if progress:
                    progress(f"공시 {nxt}까지 {n_total}건")
                cur = nxt + timedelta(days=1)
            out["filings"] = n_total
        self.crud.s.commit()
        return out

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

    def ensure_instrument(self, code: str) -> int:
        """종목표에 없으면 최소 정보로 만들어 준다. 계좌에만 있는 종목을 기록하려면 자리가 필요하다.

        kind는 unknown이라 종목 선정 대상(common)에는 절대 들어가지 않는다.
        """
        inst = self.crud.instrument_by_code().get(code)
        if inst is not None:
            return inst.id
        row = self.crud.add_instrument(Instrument(code=code, name=code, market="UNKNOWN", kind="unknown"))
        self.crud.s.commit()
        return row.id

    def current_price(self, code: str) -> int:
        """현재가. 증권사가 0이나 오류를 주면 마지막 종가로 갈음한다(수량 계산용 추정)."""
        try:
            p = self.broker.current_price(code) if self.broker is not None else 0
        except QbotError:
            p = 0
        if p > 0:
            return p
        inst = self.crud.instrument_by_code().get(code)
        bar = self.crud.last_bar(inst.id) if inst else None
        return int(bar.close) if bar else 0

    def month_ends_until(self, asof: date, n: int) -> list[str]:
        return self.crud.month_ends_until(asof.isoformat(), n)

    def closes_on(self, day: str) -> pd.Series:
        """그날 종가를 종목 코드로. 규칙 재점검이 월 수익률을 낼 때 쓴다."""
        by_id = {i.id: c for c, i in self.crud.instrument_by_code().items()}
        closes = self.crud.closes_on(day)
        return pd.Series({by_id[i]: v for i, v in closes.items() if i in by_id}, dtype="float64")

    def instrument_ids(self) -> dict[str, int]:
        """종목 코드 → 내부 id. 매매 도메인이 주문 행을 만들 때 쓴다."""
        return {c: i.id for c, i in self.crud.instrument_by_code().items()}

    def shares(self) -> pd.Series:
        return pd.Series({i.code: i.shares_outstanding for i in self.crud.instruments()}, dtype="float64")


def pit_of(asof: date) -> PointInTime:
    return PointInTime(asof)

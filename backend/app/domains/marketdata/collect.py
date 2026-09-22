"""수집과 적재 (QBOT-MS-001 MarketDataService.collect_daily, QBOT-API-001 backfill).

service.py의 조회와 분리해 둔 쓰기 쪽. MarketDataService가 이 함수들을 감싼다.
"""

from __future__ import annotations

import calendar as _cal
import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from app.core.clock import Clock
from app.core.errors import BrokerUnavailable
from app.domains.marketdata.crud import MarketDataCrud
from app.domains.marketdata.models import DailyBar, Filing, FinancialSnapshot, Instrument, InstrumentStatus
from app.domains.marketdata.ports import Bar, BrokerPort, FilingInfo, FilingPort, InstrumentInfo

ALERT_KEYWORDS = ("감사의견", "의견거절", "횡령", "배임", "거래정지", "상장폐지", "회생", "파산")
REPRT_PERIOD = {
    "11013": ("03-31", "quarter"),
    "11012": ("06-30", "quarter"),
    "11014": ("09-30", "quarter"),
    "11011": ("12-31", "annual"),
}


@dataclass
class CollectResult:
    skipped: bool = False
    bars_ok: int = 0
    bars_failed: list[str] = field(default_factory=list)
    instruments_added: int = 0
    status_changes: int = 0
    filings_added: int = 0
    series_bumped: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


class Collector:
    def __init__(
        self,
        crud: MarketDataCrud,
        broker: BrokerPort,
        filings: FilingPort,
        clock: Clock,
        held_codes: set[str] | None = None,
    ) -> None:
        self.crud, self.broker, self.filings, self.clock = crud, broker, filings, clock
        self.held_codes = held_codes or set()

    # ----- 종목·상태 -----
    def sync_instruments(self, day: str) -> tuple[int, int]:
        """마스터 파일로 종목 추가·상태 갱신. (추가 수, 상태 변경 수)"""
        by_code = self.crud.instrument_by_code()
        seen = set()
        added = changes = 0
        infos = self.broker.instrument_list()
        if len(infos) < 1000 or (by_code and len(infos) < 0.5 * len(by_code)):
            raise BrokerUnavailable(f"종목 마스터가 비정상({len(infos)}개). 상장폐지 처리를 하지 않는다")
        for info in infos:
            seen.add(info.code)
            inst = by_code.get(info.code)
            if inst is None:
                inst = self.crud.add_instrument(
                    Instrument(
                        code=info.code,
                        name=info.name,
                        market=info.market,
                        kind=info.kind,
                        listed_on=info.listed_on,
                        shares_outstanding=info.shares_outstanding,
                        shares_asof=day,
                    )
                )
                by_code[info.code] = inst
                added += 1
            else:
                if info.shares_outstanding and info.shares_outstanding != inst.shares_outstanding:
                    inst.shares_outstanding, inst.shares_asof = info.shares_outstanding, day
                if inst.name != info.name:
                    inst.name = info.name
                if inst.delisted_on is not None:
                    inst.delisted_on = None  # 재상장
            changes += self._sync_status(inst, info, day)
        # 마스터에서 사라진 종목은 상장폐지로
        for code, inst in by_code.items():
            if code not in seen and inst.delisted_on is None:
                inst.delisted_on = day
                changes += self._close_statuses(inst, day, keep=set())
        return added, changes

    def _sync_status(self, inst: Instrument, info: InstrumentInfo, day: str) -> int:
        open_ = {s.status: s for s in self.crud.open_statuses(inst.id)}
        n = 0
        for st in info.statuses - set(open_):
            self.crud.add_status(InstrumentStatus(instrument_id=inst.id, status=st, starts_on=day, ends_on=None))
            n += 1
        n += self._close_statuses(inst, day, keep=set(info.statuses), open_=open_)
        return n

    def _close_statuses(self, inst: Instrument, day: str, keep: set[str], open_=None) -> int:
        open_ = open_ if open_ is not None else {s.status: s for s in self.crud.open_statuses(inst.id)}
        n = 0
        prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        for st, row in open_.items():
            if st not in keep:
                row.ends_on = prev
                n += 1
        return n

    # ----- 일봉 -----
    def collect_bars(self, inst: Instrument, frm: str, to: str, now_iso: str) -> tuple[int, bool]:
        """frm~to 일봉을 받아 추가. 어제 종가가 저장값과 다르면 새 판으로 과거 전체를 다시 받는다.

        반환: (추가 행 수, 판이 올라갔는가)
        """
        return self.store_bars(inst, self.broker.daily_bars(inst.code, frm, to), to, now_iso)

    def store_bars(self, inst: Instrument, bars: list[Bar], to: str, now_iso: str) -> tuple[int, bool]:
        if not bars:
            return 0, False
        last = self.crud.last_bar(inst.id)
        series = last.series_no if last else 1
        bumped = False
        # 끝난 날끼리만 비교한다. 오늘 봉은 장중에 받아 두면 확정 종가와 다른데,
        # 그것은 수정주가가 아니라 아직 안 끝난 하루다. 그걸로 판을 올리면 전 종목 이력을 다시 받는다
        if last is not None and last.trade_date < to:
            hit = next((b for b in bars if b.date == last.trade_date), None)
            if hit is not None and hit.close != last.close and hit.close > 0:
                series += 1
                bumped = True
                bars = self.broker.daily_bars(inst.code, "2019-01-01", to)  # 새 판으로 전체 다시
        have = self.crud.existing_bar_dates(inst.id, series, frm=min(b.date for b in bars))
        rows = [self._to_row(inst.id, b, series, now_iso) for b in bars if b.date not in have]
        self.crud.add_bars(rows)
        self._settle_today(inst, bars, to, series, have, now_iso)
        return len(rows), bumped

    def _settle_today(self, inst: Instrument, bars: list[Bar], to: str, series: int, have: set, now_iso: str) -> None:
        """오늘 봉이 이미 있으면 확정값으로 고친다.

        과거를 고치는 것이 아니라 같은 날의 잠정값을 확정값으로 바꾸는 것이다.
        안 고치면 장중에 받아 둔 현재가가 그날 종가로 영영 남는다.
        """
        if to not in have:
            return
        fresh = next((b for b in bars if b.date == to), None)
        row = self.crud.bar_on(inst.id, to, series) if fresh else None
        if row is None or (row.close == fresh.close and row.volume == fresh.volume):
            return
        row.open, row.high, row.low, row.close = fresh.open, fresh.high, fresh.low, fresh.close
        row.volume, row.amount, row.traded = fresh.volume, fresh.amount, int(fresh.volume > 0)
        row.collected_at = now_iso

    @staticmethod
    def _to_row(instrument_id: int, b: Bar, series: int, now_iso: str) -> DailyBar:
        return DailyBar(
            instrument_id=instrument_id,
            trade_date=b.date,
            series_no=series,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            amount=b.amount,
            traded=int(b.volume > 0),
            collected_at=now_iso,
        )

    # ----- 공시·재무 -----
    def collect_filings(self, frm: str, to: str, now_iso: str) -> tuple[int, list[str]]:
        """정기공시를 받아 filing·snapshot 추가. (추가 공시 수, 돌발 키워드 알림)"""
        by_code = self.crud.instrument_by_code()
        added, alerts = 0, []
        for fi in self.filings.list_filings(frm, to):
            inst = by_code.get(fi.stock_code)
            if inst is None or self.crud.filing_by_rcept(fi.rcept_no) is not None:
                self.crud.s.commit()  # 건너뛸 때도 트랜잭션을 닫는다. 안 닫으면 창 하나 내내 잠금을 쥔다
                continue
            self.crud.s.commit()  # 외부 호출은 트랜잭션 밖에서. 전자공시 한 번에 0.5초씩 잠그면 봇이 못 들어온다
            rows = None
            if fi.reprt_code and fi.year:
                rows = self.filings.financials(fi.corp_code, fi.year, fi.reprt_code)
                if rows is None or rows.rcept_no != fi.rcept_no:
                    # 숫자가 아직 없거나(전자공시 반영 지연) 다른 본의 숫자면 공시도 넣지 않는다.
                    # 다음 수집이 같은 기간을 다시 훑어 그때 넣는다 (있는 것으로 저장하면 영원히 빠진다)
                    continue
            f = self.crud.add_filing(
                Filing(
                    rcept_no=fi.rcept_no,
                    instrument_id=inst.id,
                    report_kind=fi.report_kind,
                    rcept_date=fi.rcept_date,
                    title=fi.title,
                    collected_at=now_iso,
                )
            )
            added += 1
            if fi.stock_code in self.held_codes and any(k in fi.title for k in ALERT_KEYWORDS):
                alerts.append(f"{inst.name} {fi.title} ({fi.rcept_date})")
            if rows is not None:
                self._add_snapshots(inst, f, fi, rows)
            # 공시 하나마다 끊는다. 30일치를 한 트랜잭션으로 잡으면 적재하는 몇 분 동안
            # 봇의 다른 쓰기가 전부 "database is locked"로 막힌다
            self.crud.s.commit()
        return added, alerts

    def _add_snapshots(self, inst: Instrument, f: Filing, fi: FilingInfo, rows) -> None:
        mmdd, kind = REPRT_PERIOD[fi.reprt_code]
        period_end = f"{fi.year}-{mmdd}"
        v = rows.values
        if self.crud.snapshot_exists(f.id, period_end, kind, rows.consolidated):
            return
        self.crud.add_snapshot(
            FinancialSnapshot(
                filing_id=f.id,
                instrument_id=inst.id,
                period_end=period_end,
                period_kind=kind,
                consolidated=rows.consolidated,
                revenue=v.get("revenue"),
                operating_income=v.get("operating_income"),
                net_income=v.get("net_income"),
                net_income_owner=v.get("net_income_owner"),
                equity=v.get("equity"),
                equity_owner=v.get("equity_owner"),
            )
        )
        cum_op = rows.cumulative.get("operating_income")
        if fi.reprt_code == "11014" and cum_op is not None:
            self.crud.add_snapshot(
                FinancialSnapshot(
                    filing_id=f.id,
                    instrument_id=inst.id,
                    period_end=period_end,
                    period_kind="cum3q",
                    consolidated=rows.consolidated,
                    operating_income=cum_op,
                )
            )
        if fi.reprt_code == "11011" and v.get("operating_income") is not None:
            q3 = self.crud.cumulative_q3_op(inst.id, fi.year, rows.consolidated)
            if q3 is not None:
                self.crud.add_snapshot(
                    FinancialSnapshot(
                        filing_id=f.id,
                        instrument_id=inst.id,
                        period_end=f"{fi.year}-12-31",
                        period_kind="quarter",
                        consolidated=rows.consolidated,
                        operating_income=v["operating_income"] - q3,
                    )
                )

    # ----- 달력·지수 -----
    def collect_calendar(self, frm: str, to: str) -> int:
        days = self.broker.calendar(frm, to)
        trading = [d.date for d in days if d.is_trading_day]
        # 월말 거래일은 그 달의 마지막 달력일까지 범위에 들어온 달에만 정한다. 잘린 달은 건드리지 않는다
        covered = set()
        for d in days:
            y, m = int(d.date[:4]), int(d.date[5:7])
            if to >= f"{y:04d}-{m:02d}-{_cal.monthrange(y, m)[1]:02d}":
                covered.add(d.date[:7])
        month_end = {max(x for x in trading if x[:7] == ym) for ym in covered if any(x[:7] == ym for x in trading)}
        for d in days:
            flag = (d.date in month_end) if d.date[:7] in covered else None
            self.crud.upsert_calendar(d.date, d.is_trading_day, flag)
        return len(days)

    def collect_index(self, frm: str, to: str) -> int:
        n = 0
        for name in ("KOSPI", "KOSPI200", "KOSDAQ"):
            for d, c in self.broker.index_closes(name, frm, to).items():
                self.crud.upsert_index(name, d, c)
                n += 1
        return n

    # ----- 검증 자료의 폐지 종목 시세 -----
    def load_research_prices(self, folder: Path, now_iso: str) -> int:
        """<code>.csv (date,open,high,low,close,volume,amount) 파일들을 적재. 없는 종목은 폐지 종목으로 추가."""
        by_code = self.crud.instrument_by_code()
        n = 0
        for p in sorted(folder.glob("*.csv")):
            code = p.stem
            inst = by_code.get(code)
            if inst is None:
                inst = self.crud.add_instrument(
                    Instrument(code=code, name=code, market="KOSPI", kind="common", delisted_on="2019-01-01")
                )
                by_code[code] = inst
            have = self.crud.existing_bar_dates(inst.id, 1)
            rows = []
            for r in csv.DictReader(open(p)):
                if r["date"] in have:
                    continue
                b = Bar(
                    r["date"],
                    int(float(r["open"])),
                    int(float(r["high"])),
                    int(float(r["low"])),
                    int(float(r["close"])),
                    int(float(r["volume"])),
                    int(float(r.get("amount") or 0)),
                )
                rows.append(self._to_row(inst.id, b, 1, now_iso))
            self.crud.add_bars(rows)
            n += len(rows)
        return n

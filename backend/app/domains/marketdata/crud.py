"""시장 데이터 DB 접근. service.py만 부른다. 날짜 인자는 service가 PointInTime에서 꺼내 준다."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.marketdata.models import (
    DailyBar,
    Filing,
    FinancialSnapshot,
    IndexLevel,
    Instrument,
    InstrumentStatus,
    TradingCalendar,
)


class MarketDataCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def instruments(self) -> list[Instrument]:
        return list(self.s.scalars(select(Instrument)))

    def snapshots_until(self, rcept_until: str) -> list[tuple[FinancialSnapshot, Filing]]:
        """접수 일자가 rcept_until 이하인 공시의 재무 스냅샷 전부."""
        stmt = (
            select(FinancialSnapshot, Filing)
            .join(Filing, FinancialSnapshot.filing_id == Filing.id)
            .where(Filing.rcept_date <= rcept_until)
        )
        return [(snap, fil) for snap, fil in self.s.execute(stmt)]

    def series_asof(self, trade_until: str) -> dict[int, int]:
        """종목별로 trade_until 이전에 수집된 판 중 가장 큰 판 번호. 재현에서 그 시점의 판을 고른다."""
        stmt = (
            select(DailyBar.instrument_id, func.max(DailyBar.series_no))
            .where(DailyBar.collected_at <= trade_until + "T23:59:59")
            .group_by(DailyBar.instrument_id)
        )
        return {i: n for i, n in self.s.execute(stmt)}

    def bars_until(self, trade_until: str, first_date: str) -> list[DailyBar]:
        """first_date ~ trade_until 사이 일봉 전부(모든 판). service가 판을 고른다."""
        stmt = (
            select(DailyBar)
            .where(DailyBar.trade_date <= trade_until, DailyBar.trade_date >= first_date)
            .order_by(DailyBar.instrument_id, DailyBar.trade_date)
        )
        return list(self.s.scalars(stmt))

    def trading_dates_until(self, trade_until: str, n: int) -> list[str]:
        stmt = (
            select(DailyBar.trade_date)
            .where(DailyBar.trade_date <= trade_until)
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(n)
        )
        return sorted(self.s.scalars(stmt))

    def month_ends_until(self, day: str, n: int) -> list[str]:
        """달력에서 월말로 표시된 거래일 최근 n개. 규칙 재점검이 월 단위로 훑는다."""
        stmt = (
            select(TradingCalendar.date)
            .where(TradingCalendar.date <= day, TradingCalendar.is_month_end == 1)
            .order_by(TradingCalendar.date.desc())
            .limit(n)
        )
        return sorted(self.s.scalars(stmt))

    def closes_on(self, day: str) -> dict[int, float]:
        """그날 종가. 종목 id → 종가. 판(series_no)이 여럿이면 가장 큰 판.

        판 번호 오름차순으로 읽어 마지막 값이 남게 한다. collected_at으로 고르면 적재로 받은
        과거 일봉은 수집 시각이 전부 적재일이라 아무 판이나 뽑힌다.
        """
        rows = self.s.execute(
            select(DailyBar.instrument_id, DailyBar.series_no, DailyBar.close)
            .where(DailyBar.trade_date == day)
            .order_by(DailyBar.instrument_id, DailyBar.series_no)
        )
        return {iid: float(close) for iid, _sn, close in rows}

    def statuses_on(self, day: str) -> list[InstrumentStatus]:
        stmt = select(InstrumentStatus).where(
            InstrumentStatus.starts_on <= day,
            (InstrumentStatus.ends_on.is_(None)) | (InstrumentStatus.ends_on >= day),
        )
        return list(self.s.scalars(stmt))

    def index_month_end_closes(self, index_name: str, until: str, n: int) -> list[float]:
        """until 이하 월말 종가 n개(오름차순). 추세 필터용."""
        from app.domains.marketdata.models import IndexLevel

        stmt = (
            select(IndexLevel.date, IndexLevel.close)
            .where(IndexLevel.index_name == index_name, IndexLevel.date <= until)
            .order_by(IndexLevel.date)
        )
        by_month: dict[str, float] = {}
        for d, c in self.s.execute(stmt):
            by_month[d[:7]] = c  # 같은 달의 마지막 날이 남는다
        return list(by_month.values())[-n:]

    def has_bars_on(self, day: str) -> bool:
        return self.s.scalar(select(func.count()).select_from(DailyBar).where(DailyBar.trade_date == day)) > 0

    # ----- 수집·적재 (추가만 한다. INFRA C10) -----
    def instrument_by_code(self) -> dict[str, Instrument]:
        return {i.code: i for i in self.instruments()}

    def add_instrument(self, i: Instrument) -> Instrument:
        self.s.add(i)
        self.s.flush()
        return i

    def open_statuses(self, instrument_id: int) -> list[InstrumentStatus]:
        stmt = select(InstrumentStatus).where(
            InstrumentStatus.instrument_id == instrument_id, InstrumentStatus.ends_on.is_(None)
        )
        return list(self.s.scalars(stmt))

    def add_status(self, st: InstrumentStatus) -> None:
        self.s.add(st)

    def last_bar(self, instrument_id: int) -> DailyBar | None:
        stmt = (
            select(DailyBar)
            .where(DailyBar.instrument_id == instrument_id)
            .order_by(DailyBar.series_no.desc(), DailyBar.trade_date.desc())
            .limit(1)
        )
        return self.s.scalar(stmt)

    def bar_on(self, instrument_id: int, trade_date: str, series_no: int) -> DailyBar | None:
        stmt = select(DailyBar).where(
            DailyBar.instrument_id == instrument_id,
            DailyBar.trade_date == trade_date,
            DailyBar.series_no == series_no,
        )
        return self.s.scalar(stmt)

    def existing_bar_dates(self, instrument_id: int, series_no: int) -> set[str]:
        stmt = select(DailyBar.trade_date).where(
            DailyBar.instrument_id == instrument_id, DailyBar.series_no == series_no
        )
        return set(self.s.scalars(stmt))

    def add_bars(self, bars: list[DailyBar]) -> None:
        self.s.add_all(bars)

    def filing_by_rcept(self, rcept_no: str) -> Filing | None:
        return self.s.scalar(select(Filing).where(Filing.rcept_no == rcept_no))

    def add_filing(self, f: Filing) -> Filing:
        self.s.add(f)
        self.s.flush()
        return f

    def add_snapshot(self, snap: FinancialSnapshot) -> None:
        self.s.add(snap)

    def snapshot_exists(self, filing_id: int, period_end: str, period_kind: str, consolidated: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(FinancialSnapshot)
            .where(
                FinancialSnapshot.filing_id == filing_id,
                FinancialSnapshot.period_end == period_end,
                FinancialSnapshot.period_kind == period_kind,
                FinancialSnapshot.consolidated == consolidated,
            )
        )
        return self.s.scalar(stmt) > 0

    def cumulative_q3_op(self, instrument_id: int, year: str, consolidated: int) -> int | None:
        """3분기 보고서의 누적 영업이익. 4분기 = 연간 - 이것."""
        from app.domains.marketdata.models import FinancialSnapshot as FS

        stmt = (
            select(FS.operating_income)
            .where(
                FS.instrument_id == instrument_id,
                FS.period_end == f"{year}-09-30",
                FS.period_kind == "cum3q",
                FS.consolidated == consolidated,
            )
            .order_by(FS.id.desc())
            .limit(1)
        )
        return self.s.scalar(stmt)

    def upsert_calendar(self, day: str, is_trading: bool, is_month_end: bool | None) -> None:
        """is_month_end가 None이면 기존 값을 그대로 둔다 (잘린 달)."""
        from app.domains.marketdata.models import TradingCalendar

        row = self.s.scalar(select(TradingCalendar).where(TradingCalendar.date == day))
        if row is None:
            self.s.add(TradingCalendar(date=day, is_trading_day=int(is_trading), is_month_end=int(bool(is_month_end))))
        else:
            row.is_trading_day = int(is_trading)
            if is_month_end is not None:
                row.is_month_end = int(is_month_end)

    def calendar_between(self, frm: str, to: str) -> list[str]:
        from app.domains.marketdata.models import TradingCalendar

        stmt = (
            select(TradingCalendar.date)
            .where(TradingCalendar.date >= frm, TradingCalendar.date <= to, TradingCalendar.is_trading_day == 1)
            .order_by(TradingCalendar.date)
        )
        return list(self.s.scalars(stmt))

    def upsert_index(self, index_name: str, day: str, close: float) -> None:
        from app.domains.marketdata.models import IndexLevel

        row = self.s.scalar(select(IndexLevel).where(IndexLevel.index_name == index_name, IndexLevel.date == day))
        if row is None:
            self.s.add(IndexLevel(index_name=index_name, date=day, close=close))
        else:
            row.close = close

    def index_closes_between(self, index_name: str, frm: str, to: str) -> dict[str, float]:
        rows = self.s.execute(
            select(IndexLevel.date, IndexLevel.close).where(
                IndexLevel.index_name == index_name, IndexLevel.date >= frm, IndexLevel.date <= to
            )
        )
        return {d: float(c) for d, c in rows}

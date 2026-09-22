"""시장 데이터 DB 접근. service.py만 부른다. 날짜 인자는 service가 PointInTime에서 꺼내 준다."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.marketdata.models import (
    DailyBar,
    Filing,
    FinancialSnapshot,
    Instrument,
    InstrumentStatus,
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

    def statuses_on(self, day: str) -> list[InstrumentStatus]:
        stmt = select(InstrumentStatus).where(
            InstrumentStatus.starts_on <= day,
            (InstrumentStatus.ends_on.is_(None)) | (InstrumentStatus.ends_on >= day),
        )
        return list(self.s.scalars(stmt))

"""보고 도메인 DB 접근."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.reporting.models import MonthlyReport


class ReportingCrud:
    def __init__(self, session: Session) -> None:
        self.s = session

    def report(self, year_month: str, mode: str) -> MonthlyReport | None:
        return self.s.scalars(
            select(MonthlyReport).where(MonthlyReport.year_month == year_month, MonthlyReport.mode == mode)
        ).first()

    def upsert(
        self, year_month: str, mode: str, metrics_json: str, factors_json: str, notes: str | None
    ) -> MonthlyReport:
        row = self.report(year_month, mode)
        if row is None:
            row = MonthlyReport(
                year_month=year_month,
                mode=mode,
                metrics_json=metrics_json,
                factor_effects_json=factors_json,
                notes=notes,
            )
            self.s.add(row)
            self.s.flush()
            return row
        row.metrics_json, row.factor_effects_json, row.notes = metrics_json, factors_json, notes
        return row

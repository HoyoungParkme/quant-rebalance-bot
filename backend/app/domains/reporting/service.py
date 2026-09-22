"""보고 서비스 (QBOT-DOM-002 ReportingService).

월간 보고는 세 가지를 나란히 놓는다: 내 계좌, 코스피, 그리고 체결 오차가 없는 모의 계산값.
셋을 같이 봐야 "전략이 나빴는지, 체결이 나빴는지, 시장이 나빴는지"를 가를 수 있다
([[QBOT-UC-001#UC-A6]]).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.domains.reporting.crud import ReportingCrud
from app.domains.reporting.models import MonthlyReport
from app.domains.risk.crud import RiskCrud


@dataclass
class MonthlyMetrics:
    year_month: str
    start_total: int = 0
    end_total: int = 0
    flow: int = 0
    ret: float = 0.0  # 입출금을 뺀 수익률
    kospi_ret: float | None = None
    gap: float | None = None  # 내 수익률 - 코스피
    max_drawdown: float = 0.0
    trades: int = 0
    fees: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class ReportingService:
    def __init__(
        self,
        session: Session,
        index_closes: Callable[[str, str, str], dict[str, float]] | None = None,
        trade_stats: Callable[[str, str], tuple[int, int]] | None = None,
        positions_fn: Callable[[], list[dict]] | None = None,
    ) -> None:
        self.s = session
        self.crud = ReportingCrud(session)
        self.risk = RiskCrud(session)
        self.index_closes = index_closes or (lambda name, frm, to: {})
        self.trade_stats = trade_stats or (lambda frm, to: (0, 0))
        self.positions_fn = positions_fn or (lambda: [])

    # ----- 일일 -----
    def daily_summary(self, d: date) -> str:
        """장 마감 뒤 한 줄 요약 (QBOT-SCN-001 S1)."""
        day = d.isoformat()
        v = self.risk.valuation_on(day)
        if v is None:
            return f"{day} 평가액 기록이 없다"
        lines = [
            f"{day} 평가액 {v.total:,}원 (현금 {v.cash:,} · 주식 {v.stock_value:,} · 지수 {v.index_value:,})",
            f"고점 대비 {v.drawdown:+.1%} · 원금 대비 {v.pnl_vs_principal:+.1%}",
        ]
        if v.external_flow:
            lines.append(f"입출금 {v.external_flow:+,}원")
        rows = self.positions_fn()
        if rows:
            best = max(rows, key=lambda r: r["pnl_pct"])
            worst = min(rows, key=lambda r: r["pnl_pct"])
            lines.append(
                f"보유 {len(rows)}종목 · 최고 {best['code']} {best['pnl_pct']:+.1%}"
                f" · 최저 {worst['code']} {worst['pnl_pct']:+.1%}"
            )
        return "\n".join(lines)

    # ----- 월간 -----
    def monthly(self, year: int, month: int, mode: str) -> MonthlyReport:
        ym = f"{year:04d}-{month:02d}"
        frm, to = f"{ym}-01", f"{ym}-31"
        vals = self.risk.valuations(frm, to)
        m = MonthlyMetrics(year_month=ym)
        if not vals:
            m.notes.append("그 달의 평가액 기록이 없다")
        else:
            # 시작값은 지난달 마지막 평가액이다. 이 달 첫 기록을 쓰면 첫날 등락이 수익에서 빠진다
            prev = self.risk.last_valuation_before(vals[0].date)
            m.start_total = prev.total if prev else vals[0].total
            m.end_total = vals[-1].total
            # 직전 기록이 없으면 첫날 종가가 시작값이고 그날 입금은 이미 그 안에 들어 있다
            m.flow = sum(v.external_flow for v in (vals if prev else vals[1:]))
            base = m.start_total + m.flow
            m.ret = (m.end_total / base - 1) if base else 0.0
            m.max_drawdown = min((v.drawdown for v in vals), default=0.0)  # 그 달 중 고점 대비 최저
            closes = self.index_closes("KOSPI", vals[0].date, vals[-1].date)
            if len(closes) >= 2:
                ordered = [closes[k] for k in sorted(closes)]
                m.kospi_ret = ordered[-1] / ordered[0] - 1
                m.gap = m.ret - m.kospi_ret
            m.trades, m.fees = self.trade_stats(frm, to)
        row = self.crud.upsert(ym, mode, json.dumps(m.as_dict(), ensure_ascii=False), json.dumps({}), None)
        return row

    def describe_monthly(self, row: MonthlyReport) -> str:
        m = json.loads(row.metrics_json)
        lines = [f"{m['year_month']} 월간 보고 ({'실전' if row.mode == 'live' else '모의'})"]
        if m.get("notes"):
            lines += [f"  {n}" for n in m["notes"]]
        if m["end_total"]:
            lines.append(f"  평가액 {m['start_total']:,} → {m['end_total']:,}원 (입출금 {m['flow']:+,})")
            lines.append(f"  수익률 {m['ret']:+.2%}")
            if m.get("kospi_ret") is not None:
                lines.append(f"  코스피 {m['kospi_ret']:+.2%} · 차이 {m['gap']:+.2%}")
            lines.append(
                f"  고점 대비 최저 {m['max_drawdown']:+.1%} · 체결 {m['trades']}건 · 수수료·세금 {m['fees']:,}원"
            )
        return "\n".join(lines)

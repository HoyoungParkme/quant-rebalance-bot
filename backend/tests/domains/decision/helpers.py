"""판단 테스트용 가짜 시장 데이터. 종목 n개에 일봉 260일과 연간·분기 재무를 넣는다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domains.marketdata.models import DailyBar, Filing, FinancialSnapshot, IndexLevel, Instrument


def seed_market(session, n: int = 15, asof: str = "2025-05-30", seed: int = 0, index_trend: str = "up"):
    rng = np.random.default_rng(seed)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=asof, periods=260)]
    codes = []
    for k in range(n):
        code = f"{k + 1:06d}"
        codes.append(code)
        inst = Instrument(code=code, name=f"회사{k}", market="KOSPI", kind="common", shares_outstanding=10_000_000)
        session.add(inst)
        session.flush()
        px = 10_000 + (k + 1) * 1000 + rng.normal(0, 100, len(dates)).cumsum()
        for d, p in zip(dates, px, strict=True):
            p = int(max(p, 1500))
            session.add(
                DailyBar(
                    instrument_id=inst.id,
                    trade_date=d,
                    series_no=1,
                    open=p,
                    high=p,
                    low=p,
                    close=p,
                    volume=100_000,
                    amount=p * 100_000,
                    traded=1,
                    collected_at=d + "T18:30:00",
                )
            )
        base = 1e10 * (k + 1)
        for rc, fy in (("A1", "2023-12-31"), ("A2", "2024-12-31")):
            f = Filing(
                rcept_no=f"{rc}{code}",
                instrument_id=inst.id,
                report_kind="annual",
                rcept_date=f"{int(fy[:4]) + 1}-03-20",
                title="사업보고서",
                collected_at="2026-01-01T00:00:00",
            )
            session.add(f)
            session.flush()
            g = 1.0 if rc == "A1" else 1.0 + 0.1 * (k % 5)
            session.add(
                FinancialSnapshot(
                    filing_id=f.id,
                    instrument_id=inst.id,
                    period_end=fy,
                    period_kind="annual",
                    consolidated=1,
                    revenue=int(base * 5 * g),
                    operating_income=int(base * g),
                    net_income=int(base * 0.8 * g),
                    equity=int(base * 6),
                )
            )
        for rc, qe, rd in (("Q1", "2024-03-31", "2024-05-10"), ("Q2", "2025-03-31", "2025-05-10")):
            f = Filing(
                rcept_no=f"{rc}{code}",
                instrument_id=inst.id,
                report_kind="quarter",
                rcept_date=rd,
                title="분기보고서",
                collected_at="2026-01-01T00:00:00",
            )
            session.add(f)
            session.flush()
            session.add(
                FinancialSnapshot(
                    filing_id=f.id,
                    instrument_id=inst.id,
                    period_end=qe,
                    period_kind="quarter",
                    consolidated=1,
                    operating_income=int(base * 0.25 * (1 + 0.2 * (k % 3))),
                )
            )
    months = pd.date_range(end=asof, periods=12, freq="ME").strftime("%Y-%m-%d")
    for i, m in enumerate(months):
        close = 2500 + i * 20 if index_trend == "up" else 3000 - i * 30
        session.add(IndexLevel(index_name="KOSPI", date=m, close=close))
    session.flush()
    return codes

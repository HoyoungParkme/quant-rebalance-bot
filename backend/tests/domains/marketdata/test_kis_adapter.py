"""KisAdapter(조회) — 응답 페이지 넘기기. 실제 호출은 tests/live."""

from __future__ import annotations

from datetime import date, timedelta

from app.domains.marketdata.adapters.kis import KisAdapter


class PagedIndex:
    """지수 일별 시세를 한 번에 50개씩, 요청한 끝 날짜부터 거꾸로 준다. 실제 API와 같은 크기다."""

    def __init__(self, first: str, last: str, page: int = 50) -> None:
        d, end = date.fromisoformat(first), date.fromisoformat(last)
        self.days = []
        while d <= end:
            if d.weekday() < 5:
                self.days.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        self.page = page
        self.calls = 0

    def get(self, path, tr_id, params, tr_cont=""):
        self.calls += 1
        lo, hi = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
        rows = [d for d in self.days if lo <= d <= hi][-self.page :]
        return {"output2": [{"stck_bsop_date": d, "bstp_nmix_prpr": "2500.5"} for d in reversed(rows)]}


def test_index_closes_follows_pages_smaller_than_daily_bars():
    """지수는 한 페이지가 50개다. "100개 미만이면 끝"으로 짜면 두 달 남짓에서 멈춘다."""
    c = PagedIndex("2025-01-01", "2026-09-22")
    out = KisAdapter(c, master_fetch=lambda: []).index_closes("KOSPI", "2025-01-02", "2026-09-22")
    assert min(out) == "2025-01-02" and max(out) == "2026-09-22"
    assert len(out) == len([d for d in c.days if d >= "20250102"])
    assert c.calls > 5


def test_index_closes_stops_when_history_runs_out():
    """요청 시작일보다 자료가 짧으면 빈 응답에서 멈춘다(무한 반복하지 않는다)."""
    c = PagedIndex("2026-07-13", "2026-09-22")
    out = KisAdapter(c, master_fetch=lambda: []).index_closes("KOSPI", "2019-01-01", "2026-09-22")
    assert min(out) == "2026-07-13" and c.calls <= 3

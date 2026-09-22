"""PointInTime (QBOT-MS-001 PointInTime.filings_before)."""

from datetime import date

from app.core.pit import PointInTime


def test_filings_before_is_previous_calendar_day():
    pit = PointInTime(date(2025, 5, 30))
    assert pit.filings_before() == date(2025, 5, 29)
    assert pit.bars_until() == date(2025, 5, 30)


def test_filings_before_crosses_weekend_as_calendar_day():
    # 월요일 기준일이면 일요일. 거래일이 아니어도 접수 일자는 달력일이다.
    assert PointInTime(date(2025, 6, 2)).filings_before() == date(2025, 6, 1)

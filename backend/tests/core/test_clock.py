"""FrozenClock은 어떤 시간대로 들어와도 KST로 답한다."""

from datetime import UTC, datetime

from app.core.clock import KST, FrozenClock


def test_frozen_clock_converts_to_kst():
    c = FrozenClock(datetime(2025, 5, 30, 23, 0, tzinfo=UTC))
    assert c.now().tzinfo == KST
    assert c.today().isoformat() == "2025-05-31"


def test_frozen_clock_naive_is_kst():
    assert FrozenClock(datetime(2025, 5, 30, 8, 0)).today().isoformat() == "2025-05-30"

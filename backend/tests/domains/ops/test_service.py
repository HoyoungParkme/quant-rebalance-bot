"""OpsService: 가리기, 밀린 알림, 명령 기록 (UC-S5, UC-H1)."""

from datetime import datetime

from sqlalchemy import select

from app.core.clock import FrozenClock
from app.domains.ops.models import Alert, Command
from app.domains.ops.service import Masker, OpsService

CLOCK = FrozenClock(datetime(2025, 5, 30, 18, 30))


class FakeNotifier:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send(self, text):
        self.sent.append(text)
        return self.ok

    def fetch_commands(self, offset):
        return []


def make(session, notifier, mode="paper"):
    return OpsService(session, notifier, CLOCK, mode, Masker(["12345678", "FAKEAPPKEYVALUE0000XYZ"]))


def test_notify_masks_secrets_and_prefixes_mode(session):
    n = FakeNotifier()
    a = make(session, n, "live").notify("order", "계좌 12345678 키 FAKEAPPKEYVALUE0000XYZ 매수")
    assert n.sent == ["[실전] 계좌 ****5678 키 ****0XYZ 매수"]
    assert a.delivered == 1 and "12345678" not in a.text


def test_send_failure_is_recorded_and_flushed_later(session):
    n = FakeNotifier(ok=False)
    svc = make(session, n)
    a1 = svc.notify("error", "첫 알림")
    assert a1.delivered == 0
    n.ok = True
    svc.notify("summary", "둘째 알림")
    rows = session.scalars(select(Alert).order_by(Alert.id)).all()
    assert [r.delivered for r in rows] == [1, 1] and rows[0].deferred == 1
    assert n.sent[-2].endswith("둘째 알림") and "첫 알림" in n.sent[-1]  # 새 알림이 먼저, 밀린 것은 뒤에


def test_deferred_backlog_is_capped(session):
    n = FakeNotifier(ok=False)
    svc = make(session, n)
    for i in range(15):
        svc.notify("heartbeat", f"h{i}")
    n.ok = True
    n.sent.clear()
    svc.notify("summary", "now")
    assert len(n.sent) == 11  # 새 알림 1 + 밀린 것 최근 10건


def test_notify_without_notifier_still_records(session):
    a = make(session, None).notify("summary", "x")
    assert a.delivered == 0


def test_record_command_masks_sender_and_result(session):
    svc = make(session, FakeNotifier())
    c = svc.record_command("12345678", "status", {"a": 1}, True, None, result_text="계좌 12345678")
    row = session.get(Command, c.id)
    assert row.sender == "****5678" and row.result == "ok" and "12345678" not in row.result_text


def test_status_lines(session):
    svc = OpsService(
        session, None, CLOCK, "paper", Masker([]), state_fn=lambda: {"halted": True, "pending_decisions": 2}
    )
    out = svc.status()
    assert "모드: 모의" in out and "정지: 예" in out and "대기 중인 판단: 2" in out

"""텔레그램 입구: 파싱, 인가, 확인 코드 (UC-H1, QBOT-API-001 1장)."""

from datetime import datetime, timedelta

from app.core.clock import FrozenClock
from app.domains.ops.ports import IncomingCommand
from app.entry.telegram.poller import TelegramPoller, parse_message
from app.entry.tools import HALT_SCHEMA, REPLAY_SCHEMA, STATUS_SCHEMA, ToolRegistry, ToolSpec


class FakeNotifier:
    def __init__(self):
        self.sent, self.incoming = [], []

    def send(self, text):
        self.sent.append(text)
        return True

    def fetch_commands(self, offset):
        out = [c for c in self.incoming if offset is None or c.update_id >= offset]
        return out


def make():
    log = []
    reg = ToolRegistry(allowed_senders={"111"}, record=lambda *a: log.append(a))
    reg.add(ToolSpec("status", STATUS_SCHEMA, lambda a: "상태 OK"))
    reg.add(ToolSpec("halt", HALT_SCHEMA, lambda a: f"정지 {a.get('reason')}", needs_confirm=True))
    reg.add(ToolSpec("replay", REPLAY_SCHEMA, lambda a: f"재현 {a['asof']}"))
    n = FakeNotifier()
    p = TelegramPoller(n, reg, FrozenClock(datetime(2025, 5, 30, 10, 0)), "111")
    return p, n, log


def test_parse_forms():
    assert parse_message("/status") == ("status", {})
    assert parse_message("/halt reason=점검") == ("halt", {"reason": "점검"})
    assert parse_message("/replay 2025-05-30") == ("replay", {"asof": "2025-05-30"})
    assert parse_message("/resume reset_peak=true") == ("resume", {"reset_peak": True})
    assert parse_message("안녕") == (None, {})


def test_unauthorized_sender_gets_no_reply_but_is_recorded():
    p, n, log = make()
    assert p.handle("999", "/status") is None
    assert log[-1][0] == "999" and log[-1][3] is False and n.sent == []


def test_confirm_flow():
    p, n, log = make()
    reply = p.handle("111", "/halt reason=점검")
    assert "확인 코드" in reply
    code = reply.strip().split(": ")[-1]
    assert p.handle("111", "틀린코드") != "정지 점검"
    assert p.handle("111", code) == "정지 점검"
    assert "111" not in p.pending


def test_confirm_code_expires():
    p, n, log = make()
    reply = p.handle("111", "/halt")
    code = reply.strip().split(": ")[-1]
    p.clock = FrozenClock(datetime(2025, 5, 30, 10, 6))
    assert "만료" in p.handle("111", code)


def test_poll_once_replies_and_advances_offset():
    p, n, log = make()
    n.incoming = [IncomingCommand(10, "111", "/status", "t"), IncomingCommand(11, "111", "/replay 2025-05-30", "t")]
    assert p.poll_once() == 2 and p.offset == 12
    assert n.sent == ["상태 OK", "재현 2025-05-30"]
    assert p.poll_once() == 0
    _ = timedelta


def test_confirm_flag_in_message_is_ignored():
    p, n, log = make()
    assert "확인 코드" in p.handle("111", "/halt confirm=true reason=x")


def test_handler_exception_rolls_back_and_replies():
    calls = []
    p, n, log = make()
    p.registry.add(ToolSpec("boom", STATUS_SCHEMA, lambda a: 1 / 0))
    p.on_error = lambda: calls.append("rollback")
    p.registry.on_error = p.on_error
    n.incoming = [IncomingCommand(20, "111", "/boom", "t"), IncomingCommand(21, "111", "/status", "t")]
    assert p.poll_once() == 2 and calls == ["rollback"]
    assert n.sent[-1] == "상태 OK"


def test_non_text_update_advances_offset():
    p, n, log = make()
    n.incoming = [IncomingCommand(30, "", None, "")]
    assert p.poll_once() == 0 and p.offset == 31

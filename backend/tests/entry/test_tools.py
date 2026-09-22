"""ToolRegistry (QBOT-API-001 1장 규칙)."""

from app.entry.tools import REPLAY_SCHEMA, ToolRegistry, ToolSpec


def make():
    log = []
    reg = ToolRegistry(allowed_senders={"111"}, record=lambda *a: log.append(a))
    reg.add(ToolSpec("replay", REPLAY_SCHEMA, lambda a: f"ok {a['asof']}"))
    reg.add(
        ToolSpec(
            "halt",
            {"type": "object", "properties": {"confirm": {"type": "boolean"}}, "additionalProperties": False},
            lambda a: "halted",
            needs_confirm=True,
        )
    )
    reg.add(ToolSpec("install", {"type": "object"}, lambda a: "installed", cli_only=True))
    return reg, log


def test_unauthorized_sender_is_recorded_not_run():
    reg, log = make()
    r = reg.run("replay", {"asof": "2025-05-30"}, sender="999", via="telegram")
    assert not r.ok and r.error == "unauthorized" and log[-1][3] is False


def test_schema_validation_and_confirm():
    reg, log = make()
    assert reg.run("replay", {"asof": "2025/05/30"}, "111", "telegram").error == "invalid_args"
    assert reg.run("replay", {"asof": "2025-05-30", "x": 1}, "111", "telegram").error == "invalid_args"
    assert reg.run("halt", {}, "111", "telegram").error == "not_confirmed"
    assert reg.run("halt", {"confirm": True}, "111", "telegram").ok
    assert reg.run("replay", {"asof": "2025-05-30"}, "111", "telegram").text == "ok 2025-05-30"
    assert log[-1][4] is None


def test_result_text_is_recorded():
    reg, log = make()
    reg.run("replay", {"asof": "2025-05-30"}, "111", "telegram")
    assert log[-1][5] == "ok 2025-05-30"


def test_cli_only_and_unknown_tool():
    reg, _ = make()
    assert reg.run("install", {}, "111", "telegram").error == "precondition"
    assert reg.run("install", {}, "cli", "cli").ok
    assert reg.run("nope", {}, "cli", "cli").error == "invalid_args"

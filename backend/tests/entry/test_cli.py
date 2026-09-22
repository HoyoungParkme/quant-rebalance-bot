"""명령줄 입구: 인자 파싱 → 도구 실행."""

from datetime import date

from app.domains.decision.crud import DecisionCrud
from app.domains.decision.service import seed_default_config
from app.entry.cli import main as cli
from tests.domains.decision.helpers import seed_market


def test_cli_replay_runs_against_in_memory_app(session, monkeypatch, capsys):
    from app.core.settings import Settings
    from app.main import build

    seed_market(session)
    seed_default_config(DecisionCrud(session), "2025-01-01")
    s = Settings(
        kis_paper_app_key="k",
        kis_paper_app_secret="s",
        kis_paper_account="50212906",
        dart_api_key="d",
        telegram_bot_token="t",
        telegram_chat_id="1",
    )
    monkeypatch.setattr("app.main.build", lambda: build(s, session))
    rc = cli.main(["replay", "--asof", "2025-05-30", "--compare-to", "none"])
    out = capsys.readouterr().out
    assert rc == 0 and "2025-05-30 재현" in out
    assert date(2025, 5, 30).isoformat() in out


def test_parse_tool_args_forms():
    assert cli.parse_tool_args(["replay", "--asof=2025-05-30"]) == ("replay", {"asof": "2025-05-30"})
    assert cli.parse_tool_args(["--asof", "2025-05-30", "replay"]) == ("replay", {"asof": "2025-05-30"})
    assert cli.parse_tool_args(["halt", "--confirm"]) == ("halt", {"confirm": True})
    assert cli.parse_tool_args([]) == (None, {})

"""Settings: 필수 값이 없으면 시작을 거부한다 (QBOT-PRD-001 N4, UC-H5 1a)."""

import pytest
from pydantic import ValidationError

from app.core.settings import Settings, load_settings

REQUIRED = {
    "KIS_PAPER_APP_KEY": "k",
    "KIS_PAPER_APP_SECRET": "s",
    "KIS_PAPER_ACCOUNT": "50212906",
    "DART_API_KEY": "d",
    "TELEGRAM_BOT_TOKEN": "t",
    "TELEGRAM_CHAT_ID": "1",
}


def test_missing_required_refuses(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_full_env_loads_and_defaults_to_paper(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = load_settings(env_file=None)
    assert s.mode == "paper"
    assert s.db_path.name == "qbot-paper.sqlite3"
    assert "openapivts" in s.kis_base_url
    assert s.live_ready() is False


def test_env_file_is_read(tmp_path, monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text("\n".join(f"{k}={v}" for k, v in REQUIRED.items()) + "\nMODE=paper\n")
    assert isinstance(load_settings(env_file=f), Settings)

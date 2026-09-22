"""마이그레이션이 빈 DB에 적용되고 ERD의 테이블이 모두 생긴다 (QBOT-CODE-001 A)."""

import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_upgrade_head_on_empty_db(tmp_path):
    db = tmp_path / "t.sqlite3"
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={"QBOT_DB_PATH": str(db), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    names = {
        row[0] for row in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")
    }
    for t in [
        "instrument",
        "daily_bar",
        "filing",
        "financial_snapshot",
        "decision",
        "score",
        "trade_order",
        "fill",
        "position",
        "bot_state",
        "valuation",
        "gate_record",
        "command",
    ]:
        assert t in names
    idx = (
        sqlite3.connect(db)
        .execute("select sql from sqlite_master where name='uq_decision_real'")
        .fetchone()[0]
    )
    assert "WHERE status != 'replay'" in idx

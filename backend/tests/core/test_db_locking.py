"""동시 쓰기 (QBOT-INFRA-001 C3). 적재와 봇이 같은 파일을 쓴다."""

from __future__ import annotations

import sqlite3
import threading
import time

from app.core.db import make_engine, make_session_factory
from app.core.models_registry import metadata
from app.domains.ops.models import Alert


def _alert(text: str) -> Alert:
    return Alert(sent_at="2026-09-22T17:00:00+09:00", mode="paper", kind="summary", text=text, delivered=1)


def test_write_waits_for_another_writer_instead_of_failing(tmp_path):
    """읽고 나서 쓰면 WAL에서는 busy_timeout이 듣지 않는다(SQLITE_BUSY_SNAPSHOT).

    처음부터 쓰기 잠금을 잡지 않으면, 적재가 도는 동안 봇의 모든 명령이 즉시 실패한다.
    """
    db = tmp_path / "t.sqlite3"
    engine = make_engine(db)
    metadata.create_all(engine)
    session = make_session_factory(engine)()
    session.add(_alert("첫 줄"))
    session.commit()

    other = sqlite3.connect(db, isolation_level=None, timeout=10, check_same_thread=False)
    other.execute("PRAGMA journal_mode=WAL")
    other.execute("BEGIN IMMEDIATE")
    other.execute(
        "insert into alert (sent_at, mode, kind, text, delivered, deferred, created_at) values (?,?,?,?,?,?,?)",
        ("2026-09-22T17:00:01+09:00", "paper", "summary", "다른 연결", 1, 0, "2026-09-22T17:00:01+09:00"),
    )
    threading.Timer(0.4, other.commit).start()  # 잠시 뒤 놓아 준다 (다른 스레드에서 푼다)

    t0 = time.monotonic()
    session.query(Alert).count()  # 먼저 읽고
    session.add(_alert("봇이 쓴 줄"))  # 그 다음 쓴다
    session.commit()
    waited = time.monotonic() - t0
    other.close()

    assert 0.3 < waited < 10  # 즉시 실패하지 않고 기다렸다
    assert session.query(Alert).count() == 3

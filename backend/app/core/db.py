"""SQLAlchemy 엔진·세션. 파일 하나짜리 SQLite, WAL 모드 (QBOT-INFRA-001 3장)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: 트랜잭션 시작을 드라이버가 아니라 아래 begin 이벤트가 정한다
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 60, "isolation_level": None})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # 적재와 스케줄이 같은 파일을 쓴다. 쓰기가 겹치면 바로 실패하지 말고 기다린다
        cur.execute("PRAGMA busy_timeout=60000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):  # noqa: ANN001
        """처음부터 쓰기 잠금을 잡는다.

        WAL에서 읽다가 쓰기로 올라가는 순간 다른 쓰기와 겹치면 SQLite는 기다리지 않고 바로
        "database is locked"를 낸다(SQLITE_BUSY_SNAPSHOT). 적재가 도는 동안 봇의 명령이
        전부 실패하던 이유다. 처음부터 쓰기로 시작하면 busy_timeout이 듣는다.
        """
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """트랜잭션 경계. 서비스가 쓴다."""
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

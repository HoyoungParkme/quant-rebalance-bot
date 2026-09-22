"""여러 도메인의 models.py가 쓰는 공통 컬럼. 순수 선언만."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import KST


def now_kst_iso() -> str:
    """ORM 기본값. 서비스가 created_at을 주면 그 값이 우선이다. 재현은 FrozenClock 값을 넣는다."""
    return datetime.now(KST).isoformat(timespec="seconds")


class IdCreated:
    """id 기본 키 + created_at. 모든 테이블에 있다 (QBOT-DOM-003 0장)."""

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(25), default=now_kst_iso, nullable=False)  # KST ISO

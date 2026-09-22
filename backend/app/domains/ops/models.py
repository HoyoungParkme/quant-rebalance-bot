"""운영 도메인 ORM (QBOT-DOM-003 3.6)."""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.orm import IdCreated


class Alert(IdCreated, Base):
    __tablename__ = "alert"
    sent_at: Mapped[str] = mapped_column(String(25), nullable=False)
    mode: Mapped[str] = mapped_column(String(5), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[int] = mapped_column(Integer, nullable=False)
    deferred: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Command(IdCreated, Base):
    __tablename__ = "command"
    __table_args__ = (Index("ix_command_received", "received_at"),)
    received_at: Mapped[str] = mapped_column(String(25), nullable=False)
    sender: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(20), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(String(20))
    result_text: Mapped[str | None] = mapped_column(Text)

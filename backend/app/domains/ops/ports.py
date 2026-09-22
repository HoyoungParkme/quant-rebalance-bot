"""운영 도메인의 외부 인터페이스 (QBOT-DOM-002 NotifierPort)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IncomingCommand:
    update_id: int
    sender: str  # 대화 번호
    text: str | None  # 글자 없는 메시지(스티커 등)는 None. 오프셋만 넘긴다
    received_at: str


class NotifierPort(Protocol):
    def send(self, text: str) -> bool: ...
    def fetch_commands(self, offset: int | None) -> list[IncomingCommand]: ...

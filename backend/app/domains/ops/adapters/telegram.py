"""NotifierPort의 텔레그램 구현. 허용된 대화 번호 하나에만 보낸다."""

from __future__ import annotations

from datetime import datetime

from app.core.clock import KST
from app.domains.ops.ports import IncomingCommand
from app.infra.telegram_client import TelegramClient


class TelegramAdapter:
    def __init__(self, client: TelegramClient, chat_id: str) -> None:
        self.c = client
        self.chat_id = chat_id

    def send(self, text: str) -> bool:
        return self.c.send(self.chat_id, text)

    def fetch_commands(self, offset: int | None) -> list[IncomingCommand]:
        out = []
        for u in self.c.get_updates(offset):
            m = u.get("message") or {}
            text = m.get("text")
            chat = (m.get("chat") or {}).get("id")
            if chat is None:
                out.append(IncomingCommand(u["update_id"], "", None, ""))
                continue
            ts = datetime.fromtimestamp(m.get("date", 0), KST).isoformat(timespec="seconds")
            out.append(IncomingCommand(u["update_id"], str(chat), text, ts))
        return out

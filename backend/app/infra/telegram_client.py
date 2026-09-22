"""텔레그램 봇 API. 보내기와 가져오기(getUpdates 롱폴링). 들어오는 접속은 없다 (QBOT-INFRA-001 C4)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class TelegramClient:
    token: str
    timeout: float = 30.0
    _http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=35.0), init=False, repr=False)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send(self, chat_id: str, text: str) -> bool:
        try:
            r = self._http.post(self._url("sendMessage"), json={"chat_id": chat_id, "text": text[:4000]})
            return r.status_code == 200 and r.json().get("ok", False)
        except httpx.HTTPError:
            return False

    def get_updates(self, offset: int | None, long_poll: int = 20) -> list[dict]:
        params: dict = {"timeout": long_poll, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            r = self._http.get(self._url("getUpdates"), params=params, timeout=long_poll + 10)
            body = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        return body.get("result", []) if body.get("ok") else []

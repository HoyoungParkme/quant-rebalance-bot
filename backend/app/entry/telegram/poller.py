"""메신저 입구. 가져오기 → 인가 → 도구 실행 (QBOT-API-001 1장, UC-H1).

메시지 형식: `/도구 인자=값 인자=값` 또는 `/도구 값`. 확인이 필요한 도구는 6자리 코드를 보내고 답을 기다린다.
"""

from __future__ import annotations

import secrets
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.clock import Clock
from app.domains.ops.ports import NotifierPort
from app.entry.tools import ToolRegistry

CONFIRM_TTL = timedelta(minutes=5)
SINGLE_VALUE_ARG = {
    "replay": "asof",
    "gate_approve": "capital_krw",
    "review_approve": "review_id",
    "reconcile_accept": "reason",
}


@dataclass
class Pending:
    tool: str
    args: dict
    code: str
    expires: datetime


def parse_message(text: str) -> tuple[str | None, dict]:
    """'/halt reason=점검' → ('halt', {'reason': '점검'}). '/replay 2025-05-30' → 값 하나짜리 도구의 기본 인자."""
    text = text.strip()
    if not text.startswith("/"):
        return None, {}
    try:
        parts = shlex.split(text[1:])
    except ValueError:
        parts = text[1:].split()
    if not parts:
        return None, {}
    tool, kw = parts[0].split("@")[0], {}
    for tok in parts[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            kw[k.replace("-", "_")] = _coerce(v)
        elif tool in SINGLE_VALUE_ARG and SINGLE_VALUE_ARG[tool] not in kw:
            kw[SINGLE_VALUE_ARG[tool]] = _coerce(tok)
        else:
            kw[tok.replace("-", "_")] = True
    return tool, kw


def _coerce(v: str):
    if v.lower() in ("true", "yes", "y"):
        return True
    if v.lower() in ("false", "no", "n"):
        return False
    if v.isdigit():
        return int(v)
    try:
        return float(v) if "." in v else v
    except ValueError:
        return v


@dataclass
class TelegramPoller:
    notifier: NotifierPort
    registry: ToolRegistry
    clock: Clock
    allowed_chat: str
    offset: int | None = None
    on_error: Callable[[], None] | None = None  # 예외 뒤 정리(세션 롤백)
    pending: dict[str, Pending] = field(default_factory=dict)

    def handle(self, sender: str, text: str) -> str | None:
        """메시지 하나 처리. 답장 문자열(없으면 None). 허용되지 않은 보낸 사람에게는 답하지 않는다."""
        if sender != self.allowed_chat:
            self.registry.run("_unauthorized", {}, sender=sender, via="telegram")
            return None
        now = self.clock.now()
        p = self.pending.get(sender)
        if p and text.strip() == p.code:
            del self.pending[sender]
            if now > p.expires:
                return "확인 코드가 만료됐다. 다시 명령해라"
            res = self.registry.run(p.tool, {**p.args, "confirm": True}, sender=sender, via="telegram")
            return res.text if res.ok else f"오류({res.error}): {res.text}"
        tool, args = parse_message(text)
        if tool is None:
            return "명령은 /도구 형식이다. 예: /status"
        args.pop("confirm", None)  # 메시지에 직접 넣은 confirm은 무시한다. 확인 코드로만 채워진다
        res = self.registry.run(tool, args, sender=sender, via="telegram")
        if res.error == "not_confirmed":
            code = f"{secrets.randbelow(10**6):06d}"
            self.pending[sender] = Pending(tool, args, code, now + CONFIRM_TTL)
            summary = ", ".join(f"{k}={v}" for k, v in args.items()) or "인자 없음"
            return f"{tool} 실행 확인: {summary}\n5분 안에 확인 코드를 답해라: {code}"
        return res.text if res.ok else f"오류({res.error}): {res.text}"

    def poll_once(self) -> int:
        n = 0
        for cmd in self.notifier.fetch_commands(self.offset):
            self.offset = cmd.update_id + 1  # 글자 없는 메시지도 오프셋은 넘긴다 (아래 text None)
            if cmd.text is None:
                continue
            try:
                reply = self.handle(cmd.sender, cmd.text)
            except Exception as e:  # noqa: BLE001 - 한 명령의 실패가 다음 명령(비상 정지)을 막으면 안 된다
                if self.on_error:
                    self.on_error()  # 세션 롤백
                reply = f"오류: {e}"
            if reply:
                self.notifier.send(reply)
            n += 1
        return n

    def run_forever(self, idle_sleep: float = 1.0) -> None:
        while True:
            try:
                if self.poll_once() == 0:
                    time.sleep(idle_sleep)
            except Exception as e:  # noqa: BLE001 - 입구가 죽으면 비상 정지 경로가 사라진다
                if self.on_error:
                    self.on_error()
                print(f"poller error: {e}", flush=True)
                time.sleep(5)

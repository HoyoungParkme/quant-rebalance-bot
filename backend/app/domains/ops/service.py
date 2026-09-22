"""운영 서비스 (QBOT-DOM-002 OpsService). 알림, 명령 기록, 상태 조회.

알림 실패는 매매를 멈추지 않는다 (UC-S5). 비밀값은 가리고 보낸다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from app.core.clock import Clock
from app.domains.ops.crud import OpsCrud
from app.domains.ops.models import Alert, Command
from app.domains.ops.ports import NotifierPort

MAX_DEFERRED = 10  # 밀린 알림은 최근 10건만 다시 보낸다. 나머지는 기록에만 남는다


@dataclass
class Masker:
    """설정에 있는 비밀값과 계좌번호를 가린다. 뒤 네 자리만 남긴다."""

    secrets: list[str]

    def __call__(self, text: str) -> str:
        for s in sorted({x for x in self.secrets if x and len(x) >= 6}, key=len, reverse=True):
            text = text.replace(s, "****" + s[-4:])
        return text


class OpsService:
    def __init__(
        self,
        session,
        notifier: NotifierPort | None,
        clock: Clock,
        mode: str,
        masker: Masker,
        state_fn: Callable[[], dict] | None = None,
    ) -> None:
        self.crud = OpsCrud(session)
        self.notifier = notifier
        self.clock = clock
        self.mode = mode
        self.mask = masker
        self.state_fn = state_fn or (lambda: {})

    def notify(self, kind: str, text: str) -> Alert:
        """모드 접두어 + 가리기 + 전송. 실패하면 기록만 남기고 다음 알림 때 밀린 것을 함께 보낸다."""
        body = f"[{'실전' if self.mode == 'live' else '모의'}] {self.mask(text)}"
        now = self.clock.now().isoformat(timespec="seconds")
        delivered = 0
        if self.notifier is not None:
            delivered = int(self.notifier.send(body))  # 새 알림이 먼저. 정지 명령의 답이 밀린 알림 뒤에 오면 안 된다
            if delivered:
                for old in self.crud.undelivered()[-MAX_DEFERRED:]:
                    if self.notifier.send(f"(밀린 알림 {old.sent_at}) {old.text}"):
                        old.delivered, old.deferred = 1, 1
        return self.crud.add_alert(Alert(sent_at=now, mode=self.mode, kind=kind, text=body, delivered=delivered))

    def heartbeat(self) -> Alert:
        return self.notify("heartbeat", f"생존 {self.clock.now().strftime('%H:%M')}")

    def record_command(
        self, sender: str, tool: str, args: dict, allowed: bool, error: str | None, result_text: str | None = None
    ) -> Command:
        return self.crud.add_command(
            Command(
                received_at=self.clock.now().isoformat(timespec="seconds"),
                sender=self.mask(sender),
                allowed=int(allowed),
                tool=tool,
                args_json=json.dumps(args, ensure_ascii=False, default=str),
                result=error or "ok",
                result_text=self.mask(result_text) if result_text else None,
            )
        )

    def status(self) -> str:
        st = self.state_fn()
        lines = [f"모드: {'실전' if self.mode == 'live' else '모의'}"]
        for k, label in (("halted", "정지"), ("buy_suspended", "매수 중단"), ("orders_blocked", "주문 멈춤")):
            if k in st:
                lines.append(f"{label}: {'예' if st[k] else '아니오'}")
        for k, label in (
            ("pending_decisions", "대기 중인 판단"),
            ("last_heartbeat_at", "최근 생존 신호"),
            ("equity", "평가액"),
            ("drawdown", "고점 대비"),
        ):
            if st.get(k) is not None:
                lines.append(f"{label}: {st[k]}")
        return "\n".join(lines)

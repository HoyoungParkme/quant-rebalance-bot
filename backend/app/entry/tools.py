"""도구 등록표 (QBOT-API-001과 1:1). 메신저와 명령줄이 같은 표를 부른다 (QBOT-DOM-002 ToolRegistry)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator


@dataclass
class ToolSpec:
    name: str
    schema: dict
    handler: Callable[[dict], str]
    needs_confirm: bool = False
    cli_only: bool = False


@dataclass
class ToolResult:
    ok: bool
    text: str
    error: str | None = None


@dataclass
class ToolRegistry:
    allowed_senders: set[str]
    record: Callable[[str, str, dict, bool, str | None, str | None], None] | None = None
    on_error: Callable[[], None] | None = None
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def add(self, spec: ToolSpec) -> None:
        Draft202012Validator.check_schema(spec.schema)
        self.tools[spec.name] = spec

    def validate(self, name: str, args: dict) -> list[str]:
        spec = self.tools[name]
        return [e.message for e in Draft202012Validator(spec.schema).iter_errors(args)]

    def run(self, name: str, args: dict, sender: str, via: str = "cli") -> ToolResult:
        """인가 → 존재 → 스키마 → confirm → 기록 → 실행 (QBOT-API-001 1장)."""
        allowed = sender in self.allowed_senders or via == "cli"
        if not allowed:
            self._rec(sender, name, args, False, "unauthorized")
            return ToolResult(False, "", "unauthorized")
        spec = self.tools.get(name)
        if spec is None:
            self._rec(sender, name, args, True, "invalid_args")
            return ToolResult(False, f"없는 도구: {name}", "invalid_args")
        if spec.cli_only and via != "cli":
            self._rec(sender, name, args, True, "precondition")
            return ToolResult(False, f"{name}은 명령줄에서만 쓸 수 있다", "precondition")
        # confirm은 확인 단계에서 채워지므로, 스키마 검증은 confirm이 있다고 보고 한다
        errs = self.validate(name, {**args, "confirm": True} if spec.needs_confirm else args)
        if errs:
            self._rec(sender, name, args, True, "invalid_args")
            return ToolResult(False, "; ".join(errs), "invalid_args")
        if spec.needs_confirm and not args.get("confirm"):
            self._rec(sender, name, args, True, "not_confirmed")
            return ToolResult(False, f"{name}: confirm이 필요하다", "not_confirmed")
        try:
            text = spec.handler(args)
        except Exception as e:  # noqa: BLE001 - 도구 오류는 사람이 읽는 문장으로 돌려준다
            if self.on_error:
                self.on_error()  # 실패한 핸들러의 부분 쓰기를 기록 커밋이 같이 저장하지 않도록 먼저 롤백
            self._rec(sender, name, args, True, type(e).__name__, str(e))
            return ToolResult(False, str(e), type(e).__name__)
        self._rec(sender, name, args, True, None, text)
        return ToolResult(True, text)

    def _rec(
        self, sender: str, name: str, args: dict, allowed: bool, error: str | None, text: str | None = None
    ) -> None:
        if self.record:
            self.record(sender, name, json.loads(json.dumps(args, default=str)), allowed, error, text)


REPLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "asof": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "compare_to": {"type": "string", "enum": ["stored", "research_file", "none"], "default": "stored"},
    },
    "required": ["asof"],
    "additionalProperties": False,
}

BACKFILL_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "sources": {"type": "string", "description": "쉼표로 구분: bars,filings,status,calendar,index"},
        "research_prices_dir": {"type": "string"},
    },
    "required": ["from"],
    "additionalProperties": False,
}
INSTALL_SCHEMA = {
    "type": "object",
    "properties": {"register_autostart": {"type": "boolean", "default": False}},
    "additionalProperties": False,
}

STATUS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
HALT_SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string", "maxLength": 200}, "confirm": {"type": "boolean"}},
    "required": ["confirm"],
    "additionalProperties": False,
}
RESUME_SCHEMA = {
    "type": "object",
    "properties": {"reset_peak": {"type": "boolean"}, "confirm": {"type": "boolean"}},
    "required": ["confirm"],
    "additionalProperties": False,
}
TELEGRAM_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
RECONCILE_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
RUN_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
REVIEW_RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "asof": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "months": {"type": "integer", "minimum": 7, "maximum": 120},
    },
    "additionalProperties": False,
}
REVIEW_APPROVE_SCHEMA = {
    "type": "object",
    "properties": {"review_id": {"type": "integer", "minimum": 1}, "confirm": {"type": "boolean"}},
    "required": ["review_id", "confirm"],
    "additionalProperties": False,
}
POSITIONS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
RECONCILE_ACCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "minLength": 1, "maxLength": 200},
        # 차액 중 진짜 입출금인 금액. 기본 0 = "우리 기록이 틀렸다"(원금·고점 기준선을 건드리지 않는다)
        "external_flow": {"type": "integer"},
        "confirm": {"type": "boolean"},
    },
    "required": ["reason", "confirm"],
    "additionalProperties": False,
}
# decide·execute는 API-001에 없다. 스케줄(슬라이스 E)이 생기기 전까지 손으로 돌리기 위한 명령줄 전용이다
DECIDE_SCHEMA = {
    "type": "object",
    "properties": {"asof": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}},
    "required": ["asof"],
    "additionalProperties": False,
}
EXECUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "asof": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "confirm": {"type": "boolean"},
    },
    "required": ["confirm"],
    "additionalProperties": False,
}

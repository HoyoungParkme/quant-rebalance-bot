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
    record: Callable[[str, str, dict, bool, str | None], None] | None = None
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
        errs = self.validate(name, args)
        if errs:
            self._rec(sender, name, args, True, "invalid_args")
            return ToolResult(False, "; ".join(errs), "invalid_args")
        if spec.needs_confirm and not args.get("confirm"):
            self._rec(sender, name, args, True, "not_confirmed")
            return ToolResult(False, f"{name}: confirm이 필요하다", "not_confirmed")
        try:
            text = spec.handler(args)
        except Exception as e:  # noqa: BLE001 - 도구 오류는 사람이 읽는 문장으로 돌려준다
            self._rec(sender, name, args, True, type(e).__name__)
            return ToolResult(False, str(e), type(e).__name__)
        self._rec(sender, name, args, True, None)
        return ToolResult(True, text)

    def _rec(self, sender: str, name: str, args: dict, allowed: bool, error: str | None) -> None:
        if self.record:
            self.record(sender, name, json.loads(json.dumps(args, default=str)), allowed, error)


REPLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "asof": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "compare_to": {"type": "string", "enum": ["stored", "research_file", "none"], "default": "stored"},
    },
    "required": ["asof"],
    "additionalProperties": False,
}

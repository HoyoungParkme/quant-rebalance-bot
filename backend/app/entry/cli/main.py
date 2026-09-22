"""명령줄 입구. `qbot <도구> --인자 값`. 도구 목록은 entry/tools.py (QBOT-API-001)."""

from __future__ import annotations

import argparse
import json
import sys


def parse_tool_args(tokens: list[str]) -> tuple[str | None, dict]:
    """첫 번째 '--' 아닌 토큰이 도구. 나머지는 --이름 값 / --이름=값 / --플래그."""
    tool = None
    kw: dict = {}
    key = None
    for tok in tokens:
        if tok.startswith("--"):
            body = tok[2:]
            if "=" in body:
                k, v = body.split("=", 1)
                kw[k.replace("-", "_")] = v
                key = None
            else:
                key = body.replace("-", "_")
                kw[key] = True
        elif key is not None:
            kw[key] = tok
            key = None
        elif tool is None:
            tool = tok
        else:
            kw.setdefault("_positional", []).append(tok)
    return tool, kw


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="qbot", description="퀀트 리밸런싱 봇")
    p.add_argument("tool", nargs="?", help="도구 이름 (replay, …)")
    p.add_argument("--json", action="store_true", help="결과를 JSON으로")
    p.epilog = "도구 인자는 --이름 값 또는 --이름=값. 예: qbot replay --asof 2025-05-30"
    as_json = "--json" in args
    tool, kw = parse_tool_args([a for a in args if a != "--json"])
    if not tool:
        p.print_help()
        return 0
    from app.main import build

    app = build()
    res = app.tools.run(tool, kw, sender="cli", via="cli")
    if as_json:
        print(json.dumps({"ok": res.ok, "text": res.text, "error": res.error}, ensure_ascii=False))
    elif res.ok:
        print(res.text)
    else:
        print(f"오류({res.error}): {res.text}", file=sys.stderr)
    return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

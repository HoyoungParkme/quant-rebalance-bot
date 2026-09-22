"""명령줄 입구. 도구 등록표(entry/tools.py)는 슬라이스 B3에서 생긴다. 지금은 안내만 한다."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help", "help"):
        print("qbot: 도구는 아직 없습니다. QBOT-API-001의 도구가 슬라이스 B3부터 추가됩니다.")
        return 0
    print("qbot: 아직 구현되지 않은 명령입니다. `qbot --help`", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

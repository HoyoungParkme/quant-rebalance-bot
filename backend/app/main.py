"""조립. 설정·DB·서비스·도구 등록표를 만든다. 입구 셋이 이것을 부른다."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.db import make_engine, make_session_factory
from app.core.settings import Settings, load_settings
from app.domains.decision.service import DecisionService, Portfolio
from app.domains.marketdata.service import MarketDataService
from app.entry.tools import REPLAY_SCHEMA, ToolRegistry, ToolSpec


def code_version() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True
            ).stdout.strip()
            or "unknown"
        )
    except Exception:  # noqa: BLE001
        return "unknown"


@dataclass
class App:
    settings: Settings
    session: Session
    clock: Clock
    md: MarketDataService
    decision: DecisionService
    tools: ToolRegistry


def build(settings: Settings | None = None, session: Session | None = None) -> App:
    settings = settings or load_settings()
    if session is None:
        session = make_session_factory(make_engine(settings.db_path))()
    clock = Clock()
    md = MarketDataService(session)

    def portfolio() -> Portfolio:
        # 매매 도메인(슬라이스 D2)이 생기기 전까지는 계획 자금을 쓴다
        return Portfolio(total=settings.planned_capital, index_value=0)

    decision = DecisionService(session, md, portfolio, code_version())
    tools = ToolRegistry(allowed_senders={settings.telegram_chat_id})

    def replay_handler(args: dict) -> str:
        r = decision.replay(date.fromisoformat(args["asof"]), args.get("compare_to", "stored"))
        session.commit()
        head = f"{r.asof} 재현: 선정 {len(r.picked)}종목 {', '.join(r.picked) or '없음'}"
        if r.matched is None:
            return head + f"\n비교 없음 ({r.compared_with}): " + "; ".join(r.diff)
        return head + (
            f"\n{r.compared_with}와 일치"
            if r.matched
            else f"\n{r.compared_with}와 다름: " + "; ".join(r.diff)
        )

    tools.add(ToolSpec("replay", REPLAY_SCHEMA, replay_handler))
    return App(settings, session, clock, md, decision, tools)

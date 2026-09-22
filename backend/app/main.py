"""조립. 설정·DB·서비스·도구 등록표를 만든다. 입구 셋이 이것을 부른다."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import models_registry  # noqa: F401 - 모든 ORM을 등록해야 도메인 간 외래 키가 풀린다
from app.core.clock import Clock
from app.core.db import make_engine, make_session_factory
from app.core.settings import Settings, load_settings
from app.domains.decision.crud import DecisionCrud
from app.domains.decision.service import DecisionService, Portfolio, seed_default_config
from app.domains.marketdata.adapters.dart import DartAdapter
from app.domains.marketdata.adapters.kis import KisAdapter
from app.domains.marketdata.service import MarketDataService
from app.entry.tools import BACKFILL_SCHEMA, INSTALL_SCHEMA, REPLAY_SCHEMA, ToolRegistry, ToolSpec
from app.infra.dart_client import DartClient
from app.infra.kis_client import KisClient


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
    base, key, secret, rate = settings.query_credentials()
    kis = KisClient(
        base,
        key,
        secret,
        rate_per_sec=rate,
        token_cache=settings.cache_dir / f"kis-token-{'live' if 'openapi.' in base else 'paper'}.json",
    )
    broker = KisAdapter(kis)
    filings = DartAdapter(DartClient(settings.dart_api_key, corp_cache=settings.cache_dir / "dart-corp.json"))
    md = MarketDataService(session, broker=broker, filings=filings, clock=clock)

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
            f"\n{r.compared_with}와 일치" if r.matched else f"\n{r.compared_with}와 다름: " + "; ".join(r.diff)
        )

    def backfill_handler(args: dict) -> str:
        sources = [x.strip() for x in str(args.get("sources", "bars,filings,status,calendar,index")).split(",")]
        rd = Path(args["research_prices_dir"]) if args.get("research_prices_dir") else None
        out = md.backfill(date.fromisoformat(args["from"]), sources, rd, progress=lambda m: print(m, flush=True))
        lines = [f"{k}: {len(v)}건 실패" if isinstance(v, list) else f"{k}: {v}" for k, v in out.items()]
        return "적재 결과\n" + "\n".join(lines)

    def install_handler(args: dict) -> str:
        lines = [
            f"모드: {settings.mode}",
            f"DB: {settings.db_path}",
            f"조회 키: {'실전' if 'openapi.' in base else '모의'}",
        ]
        try:
            kis.token()
            lines.append("증권사 접속: OK")
        except Exception as e:  # noqa: BLE001
            lines.append(f"증권사 접속: 실패 {e}")
        try:
            n = len(filings.c.corp_codes())
            lines.append(f"전자공시 접속: OK (회사 코드 {n}개)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"전자공시 접속: 실패 {e}")
        if DecisionCrud(session).config_effective("9999-12-31") is None:
            seed_default_config(DecisionCrud(session), "2019-01-01")
            session.commit()
            lines.append("전략 설정: 기본값 생성 (QBOT-PRD-001 7장)")
        else:
            lines.append("전략 설정: 있음")
        if args.get("register_autostart"):
            lines.append("자동 시작 등록: 슬라이스 E에서 지원")
        return "\n".join(lines)

    tools.add(ToolSpec("replay", REPLAY_SCHEMA, replay_handler))
    tools.add(ToolSpec("backfill", BACKFILL_SCHEMA, backfill_handler, cli_only=True))
    tools.add(ToolSpec("install", INSTALL_SCHEMA, install_handler, cli_only=True))
    return App(settings, session, clock, md, decision, tools)

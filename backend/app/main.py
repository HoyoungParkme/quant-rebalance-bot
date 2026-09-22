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
from app.domains.ops.adapters.telegram import TelegramAdapter
from app.domains.ops.service import Masker, OpsService
from app.domains.risk.service import OrderGate, RiskService
from app.domains.trading.adapters.kis import KisOrderAdapter
from app.domains.trading.service import TradingService
from app.entry.telegram.poller import TelegramPoller
from app.entry.tools import (
    BACKFILL_SCHEMA,
    HALT_SCHEMA,
    INSTALL_SCHEMA,
    REPLAY_SCHEMA,
    RESUME_SCHEMA,
    STATUS_SCHEMA,
    TELEGRAM_SCHEMA,
    ToolRegistry,
    ToolSpec,
)
from app.infra.dart_client import DartClient
from app.infra.kis_client import KisClient
from app.infra.telegram_client import TelegramClient


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
    ops: OpsService
    risk: RiskService
    trading: TradingService
    tools: ToolRegistry
    poller: TelegramPoller


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
    # 주문은 조회와 다른 키를 쓴다. 모의 모드면 모의 계좌로만 나간다 (QBOT-INFRA-001 C6)
    obase, okey, osecret, oacct, oprod, orate = settings.order_credentials()
    order_kis = KisClient(
        obase,
        okey,
        osecret,
        rate_per_sec=orate,
        token_cache=settings.cache_dir / f"kis-token-{'live' if 'openapi.' in obase else 'paper'}.json",
    )
    order_broker = KisOrderAdapter(order_kis, oacct, oprod, settings.mode, clock)
    filings = DartAdapter(DartClient(settings.dart_api_key, corp_cache=settings.cache_dir / "dart-corp.json"))
    md = MarketDataService(session, broker=broker, filings=filings, clock=clock)

    def portfolio() -> Portfolio:
        # 매매 도메인(슬라이스 D2)이 생기기 전까지는 계획 자금을 쓴다
        return Portfolio(total=settings.planned_capital, index_value=0)

    masker = Masker(
        [
            settings.kis_paper_account,
            settings.kis_live_account,
            settings.kis_paper_app_key,
            settings.kis_paper_app_secret,
            settings.kis_live_app_key,
            settings.kis_live_app_secret,
            settings.dart_api_key,
            settings.telegram_bot_token,
        ]
    )
    trading = TradingService(
        session,
        order_broker,
        OrderGate(session, settings.max_position_weight, settings.daily_order_cap_multiple),
        clock,
        md.instrument_ids,
    )
    risk = RiskService(session, clock, settings.mode, cancel_open_orders=trading.cancel_open)
    notifier = TelegramAdapter(TelegramClient(settings.telegram_bot_token), settings.telegram_chat_id)
    ops = OpsService(
        session,
        notifier,
        clock,
        settings.mode,
        masker,
        state_fn=lambda: {**risk.state_dict(), "pending_decisions": len(decision.crud.pending())},
    )

    def notify(kind: str, text: str) -> None:
        ops.notify(kind, text)
        session.commit()

    decision = DecisionService(session, md, portfolio, code_version(), notifier=notify)

    def record(sender, tool, args, allowed, error, text=None):
        ops.record_command(sender, tool, args, allowed, error, result_text=text)
        session.commit()

    tools = ToolRegistry(allowed_senders={settings.telegram_chat_id}, record=record, on_error=session.rollback)

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
            b = trading.balance()
            lines.append(f"주문 계좌({settings.mode}): OK 예수금 {b.cash:,}원, 보유 {len(b.holdings)}종목")
        except Exception as e:  # noqa: BLE001
            lines.append(f"주문 계좌({settings.mode}): 실패 {e}")
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

    def status_handler(args: dict) -> str:
        return ops.status()

    def halt_handler(args: dict) -> str:
        _, cancelled = risk.halt(args.get("reason"))
        session.commit()
        ops.notify("error", f"정지됨. 사유: {args.get('reason') or '없음'}. 미체결 취소 {cancelled}건")
        session.commit()
        return f"정지됨. 미체결 취소 {cancelled}건. 수집과 기록은 계속한다"

    def resume_handler(args: dict) -> str:
        # 평가액은 매매 도메인(D2)이 준다. 그 전까지는 고점을 0으로 두어 다음 평가에서 새로 잡히게 한다
        st = risk.resume(bool(args.get("reset_peak")), current_equity=0 if args.get("reset_peak") else None)
        session.commit()
        return "재개됨" + (
            f" (고점 재설정: {st.peak_equity:,}원, 다음 평가 때 새로 잡힘)" if args.get("reset_peak") else ""
        )

    def telegram_handler(args: dict) -> str:
        poller.run_forever()
        return "종료"

    tools.add(ToolSpec("status", STATUS_SCHEMA, status_handler))
    tools.add(ToolSpec("halt", HALT_SCHEMA, halt_handler, needs_confirm=True))
    tools.add(ToolSpec("resume", RESUME_SCHEMA, resume_handler, needs_confirm=True))
    tools.add(ToolSpec("telegram", TELEGRAM_SCHEMA, telegram_handler, cli_only=True))
    tools.add(ToolSpec("replay", REPLAY_SCHEMA, replay_handler))
    tools.add(ToolSpec("backfill", BACKFILL_SCHEMA, backfill_handler, cli_only=True))
    tools.add(ToolSpec("install", INSTALL_SCHEMA, install_handler, cli_only=True))
    poller = TelegramPoller(notifier, tools, clock, settings.telegram_chat_id, on_error=session.rollback)
    return App(settings, session, clock, md, decision, ops, risk, trading, tools, poller)

"""조립. 설정·DB·서비스·도구 등록표를 만든다. 입구 셋이 이것을 부른다."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import models_registry  # noqa: F401 - 모든 ORM을 등록해야 도메인 간 외래 키가 풀린다
from app.core.clock import Clock
from app.core.db import make_engine, make_session_factory
from app.core.settings import Settings, load_settings
from app.domains.decision.crud import DecisionCrud
from app.domains.decision.service import DecisionService, Portfolio, plan_of, seed_default_config
from app.domains.marketdata.adapters.dart import DartAdapter
from app.domains.marketdata.adapters.kis import KisAdapter
from app.domains.marketdata.service import MarketDataService, pit_of
from app.domains.ops.adapters.telegram import TelegramAdapter
from app.domains.ops.service import Masker, OpsService
from app.domains.reporting.service import ReportingService
from app.domains.risk.service import OrderGate, RiskService, ValuationRecorder
from app.domains.trading.adapters.kis import KisOrderAdapter
from app.domains.trading.service import INDEX_ETF_CODE, TradingService
from app.entry.schedule.jobs import Jobs, build_scheduler
from app.entry.telegram.poller import TelegramPoller
from app.entry.tools import (
    BACKFILL_SCHEMA,
    DECIDE_SCHEMA,
    EXECUTE_SCHEMA,
    HALT_SCHEMA,
    INSTALL_SCHEMA,
    POSITIONS_SCHEMA,
    RECONCILE_ACCEPT_SCHEMA,
    RECONCILE_SCHEMA,
    REPLAY_SCHEMA,
    RESUME_SCHEMA,
    REVIEW_APPROVE_SCHEMA,
    REVIEW_RUN_SCHEMA,
    RUN_SCHEMA,
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
    valuation: ValuationRecorder
    reporting: ReportingService
    tools: ToolRegistry
    poller: TelegramPoller
    lock: threading.RLock


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
        """판단 예산의 바탕. 계좌를 못 읽으면 계획 자금으로 갈음한다(판단은 멈추지 않는다)."""
        try:
            bal = trading.balance()
        except Exception:  # noqa: BLE001 - 계좌를 못 읽었다고 판단까지 멈추지 않는다
            return Portfolio(total=settings.planned_capital, index_value=0)
        idx = bal.holdings.get(INDEX_ETF_CODE)
        return Portfolio(total=bal.total_equity or settings.planned_capital, index_value=idx.value if idx else 0)

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

    def halted_codes() -> set[str]:
        st = md.statuses_on(pit_of(clock.today()))
        return {c for c, v in st.items() if "halted" in v}

    trading = TradingService(
        session,
        order_broker,
        OrderGate(session, settings.max_position_weight, settings.daily_order_cap_multiple),
        clock,
        md.instrument_ids,
        mode=settings.mode,
        plan_fn=lambda d: plan_of(DecisionCrud(session), d),
        state_fn=lambda: risk.state_dict(),
        ensure_instrument_fn=md.ensure_instrument,
        price_fn=md.current_price,
        halted_codes_fn=halted_codes,
        block_orders_fn=lambda reason: risk.block_orders(reason),
        unblock_orders_fn=lambda: risk.unblock_orders(),
        notify=lambda kind, text: notify(kind, text),
    )
    risk = RiskService(session, clock, settings.mode, cancel_open_orders=trading.cancel_open)
    valuation = ValuationRecorder(
        risk,
        trading.balance,
        flow_fn=trading.crud.accepted_flow_on,
        notify=lambda kind, text: notify(kind, text),
        max_drawdown=settings.max_drawdown,
    )
    reporting = ReportingService(
        session,
        index_closes=lambda name, frm, to: md.crud.index_closes_between(name, frm, to),
        trade_stats=trading.crud.trade_stats,
        positions_fn=trading.positions,
    )
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

    decision = DecisionService(
        session,
        md,
        portfolio,
        code_version(),
        replay_portfolio=Portfolio(total=settings.planned_capital, index_value=0),
        notifier=notify,
    )

    built: dict[str, App] = {}
    last_command: dict[str, int] = {}

    def record(sender, tool, args, allowed, error, text=None):
        c = ops.record_command(sender, tool, args, allowed, error, result_text=text)
        last_command["id"] = c.id
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
            lines.append(register_autostart())
        return "\n".join(lines)

    def status_handler(args: dict) -> str:
        text = ops.status()
        if risk.state_dict()["orders_blocked"]:
            rec = trading.crud.last_mismatch()
            if rec is not None:
                text += "\n" + trading.describe_reconciliation(rec)
        return text

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

    def review_run_handler(args: dict) -> str:
        asof = date.fromisoformat(args["asof"]) if args.get("asof") else clock.today()
        row = decision.review_rules(asof, int(args.get("months", 36)))
        session.commit()
        return decision.describe_review(row)

    def review_approve_handler(args: dict) -> str:
        cfg = decision.approve_review(int(args["review_id"]), command_id=last_command.get("id"))
        session.commit()
        return f"새 전략 설정 {cfg.id}. {cfg.effective_from}부터 적용: {cfg.factors_json}"

    def run_handler(args: dict) -> str:
        """상주 프로세스. 스케줄과 메신저 입구를 함께 돌린다 (QBOT-INFRA-001 C3)."""
        app = built["app"]
        jobs = Jobs(app, lock=app.lock)
        jobs.run("재시작 정리", jobs.resume_after_restart, trading_only=False)
        sched = build_scheduler(app, jobs)
        sched.start()
        notify("summary", "봇 시작. 스케줄과 명령 대기 중")
        try:
            poller.run_forever()
        finally:
            sched.shutdown(wait=False)
        return "종료"

    def telegram_handler(args: dict) -> str:
        poller.run_forever()
        return "종료"

    tools.add(ToolSpec("status", STATUS_SCHEMA, status_handler))
    tools.add(ToolSpec("halt", HALT_SCHEMA, halt_handler, needs_confirm=True))
    tools.add(ToolSpec("resume", RESUME_SCHEMA, resume_handler, needs_confirm=True))
    tools.add(ToolSpec("telegram", TELEGRAM_SCHEMA, telegram_handler, cli_only=True))

    def reconcile_handler(args: dict) -> str:
        return trading.describe_reconciliation(trading.reconcile())

    def reconcile_accept_handler(args: dict) -> str:
        flow = int(args.get("external_flow", 0))
        rec = trading.accept_reconciliation(args["reason"], command_id=last_command.get("id"), external_flow=flow)
        tail = f" 입출금 {flow:+,}원으로 기록" if flow else " (입출금 아님 — 원금·고점은 그대로)"
        return f"계좌 기준으로 맞췄다. 주문 멈춤 해제.{tail} (대조 {rec.id})"

    def positions_handler(args: dict) -> str:
        rows = trading.positions()
        if not rows:
            return "보유 종목 없음"
        head = f"보유 {len(rows)}종목"
        body = [
            f"{r['code']} {r['qty']}주 평단 {r['avg_cost']:,} 현재 {r['price']:,} "
            f"평가 {r['value']:,} 손익 {r['pnl']:+,}({r['pnl_pct']:+.1%})"
            for r in rows
        ]
        return "\n".join([head, *body])

    def decide_handler(args: dict) -> str:
        d = decision.decide_month_end(date.fromisoformat(args["asof"]), settings.mode)
        session.commit()
        picks = DecisionCrud(session).picks(d.id)
        return f"{d.asof} 판단 {d.status}: {len(picks)}종목 {', '.join(picks) or '없음'}"

    def execute_handler(args: dict) -> str:
        asof = args.get("asof")
        pend = DecisionCrud(session).pending()
        d = next((x for x in pend if x.asof == asof), None) if asof else (pend[0] if pend else None)
        if d is None:
            return "실행할 판단이 없다. 먼저 decide 를 돌린다"
        res = trading.execute(d)
        session.commit()
        return f"{d.asof} 실행: {res.summary()}"

    tools.add(ToolSpec("reconcile", RECONCILE_SCHEMA, reconcile_handler))
    tools.add(ToolSpec("reconcile_accept", RECONCILE_ACCEPT_SCHEMA, reconcile_accept_handler, needs_confirm=True))
    tools.add(ToolSpec("positions", POSITIONS_SCHEMA, positions_handler))
    tools.add(ToolSpec("decide", DECIDE_SCHEMA, decide_handler, cli_only=True))
    tools.add(ToolSpec("execute", EXECUTE_SCHEMA, execute_handler, needs_confirm=True, cli_only=True))
    tools.add(ToolSpec("review_run", REVIEW_RUN_SCHEMA, review_run_handler))
    tools.add(ToolSpec("review_approve", REVIEW_APPROVE_SCHEMA, review_approve_handler, needs_confirm=True))
    tools.add(ToolSpec("run", RUN_SCHEMA, run_handler, cli_only=True))
    tools.add(ToolSpec("replay", REPLAY_SCHEMA, replay_handler))
    tools.add(ToolSpec("backfill", BACKFILL_SCHEMA, backfill_handler, cli_only=True))
    tools.add(ToolSpec("install", INSTALL_SCHEMA, install_handler, cli_only=True))
    lock = threading.RLock()  # 스케줄 스레드와 메신저 입구가 같은 세션을 쓴다. 한 번에 하나만
    poller = TelegramPoller(notifier, tools, clock, settings.telegram_chat_id, lock=lock, on_error=session.rollback)
    built["app"] = App(
        settings, session, clock, md, decision, ops, risk, trading, valuation, reporting, tools, poller, lock
    )
    return built["app"]


AUTOSTART_UNIT = """[Unit]
Description=QBOT 퀀트 리밸런싱 봇
After=network-online.target

[Service]
Type=simple
WorkingDirectory={cwd}
ExecStart={exe} run
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""


def register_autostart() -> str:
    """부팅 때 봇이 뜨게 한다 (QBOT-INFRA-001 8.2). systemd가 없으면 방법만 알려 준다."""
    exe = shutil.which("qbot") or str(Path(sys.executable).with_name("qbot"))
    if not Path(exe.split()[0]).exists():  # python -m 으로 부르면 argv[0]는 모듈 경로라 못 쓴다
        exe = f"{sys.executable} -m app.entry.cli.main"
    unit = AUTOSTART_UNIT.format(cwd=Path(__file__).resolve().parents[1], exe=exe)
    if not shutil.which("systemctl"):
        return (
            "자동 시작 등록: systemd가 없다. 윈도우 작업 스케줄러에 "
            f'"{exe} run"을 부팅 시 실행으로 등록한다 (WSL이면 wsl.exe -e 로 감싼다)'
        )
    path = Path.home() / ".config" / "systemd" / "user" / "qbot.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
    out = subprocess.run(["systemctl", "--user", "enable", "qbot.service"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        why = out.stderr.strip()[:80]
        return f"자동 시작 등록: {path} 를 만들었다. 직접 켜야 한다 — systemctl --user enable --now qbot ({why})"
    return f"자동 시작 등록: {path} 등록됨. 로그인 없이도 돌게 하려면 loginctl enable-linger {Path.home().name}"

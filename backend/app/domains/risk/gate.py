"""실전 전환 관문 (QBOT-PRD-001 R8, QBOT-UC-001 UC-H2).

조건을 채워도 자동으로 넘어가지 않는다. 사람이 승인해야 한다.
그리고 설정 파일의 모드만 실전으로 바꿔서는 켜지지 않는다 — 통과 기록이 DB에 있어야 한다.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import command
from app.core.clock import Clock
from app.core.db import make_engine
from app.core.errors import BrokerUnavailable, GateFailed, Precondition
from app.domains.decision.models import Decision
from app.domains.risk.crud import RiskCrud
from app.domains.risk.models import BotState, GateRecord
from app.domains.risk.service import GATE_REASONS
from app.domains.trading.models import TradeOrder

MIN_PAPER_DAYS = 90  # 3개월
MIN_REBALANCES = 3
MAX_ORDER_ERRORS = 0
MIN_SELECTION_MATCH = 1.0  # 검증 코드와 100%
MAX_RETURN_GAP = 0.01  # 1%p
UNMEASURED = -1.0  # 기록에 남기는 "못 쟀음". 일치율·수익차는 음수가 될 수 없다


@dataclass
class Condition:
    name: str
    value: float
    ok: bool
    text: str


@dataclass
class GateResult:
    conditions: list[Condition] = field(default_factory=list)
    record: GateRecord | None = None

    @property
    def passed(self) -> bool:
        return bool(self.conditions) and all(c.ok for c in self.conditions)

    def describe(self) -> str:
        head = "실전 전환 관문: " + ("통과" if self.passed else "미달")
        lines = [head] + [f"  {'O' if c.ok else 'X'} {c.name}: {c.text}" for c in self.conditions]
        if self.passed and self.record is not None:
            lines.append(f"  승인하려면 gate_approve --capital-krw <금액> --confirm (기록 {self.record.id})")
        return "\n".join(lines)


class LiveGate:
    def __init__(
        self,
        session: Session,
        clock: Clock,
        mode: str,
        replay_matches: Callable[[str], bool | None] | None = None,
        month_gap: Callable[[Decision], float | None] | None = None,
        live_ready: Callable[[], bool] | None = None,
        live_probe: Callable[[], object] | None = None,
        seed_live: Callable[[GateRecord, int, str], None] | None = None,
        notify: Callable[[str, str], None] | None = None,
    ) -> None:
        self.s = session
        self.crud = RiskCrud(session)
        self.clock = clock
        self.mode = mode
        self.replay_matches = replay_matches or (lambda asof: None)
        self.month_gap = month_gap or (lambda d: None)
        self.live_ready = live_ready or (lambda: False)
        self.live_probe = live_probe
        self.seed_live = seed_live
        self.notify = notify

    def _now(self) -> str:
        return self.clock.now().isoformat(timespec="seconds")

    # ----- 집계 -----
    def _decisions(self) -> list[Decision]:
        return list(
            self.s.scalars(
                select(Decision)
                .where(Decision.mode == "paper", Decision.status.in_(("done", "partial")))
                .order_by(Decision.asof)
            )
        )

    def _paper_days(self) -> int:
        """실제로 돌린 기간. 재현(replay) 행은 과거 날짜를 갖고 있어 빼야 한다."""
        first = self.s.scalars(
            select(Decision.asof).where(Decision.mode == "paper", Decision.status != "replay").order_by(Decision.asof)
        ).first()
        if first is None:
            return 0
        return (self.clock.today() - date.fromisoformat(first)).days

    def _order_errors(self, since: str) -> int:
        """증권사 쪽 오류만, 평가 구간 안에서 센다.

        관문이 막은 주문은 봇이 제대로 일한 것이라 오류가 아니다(사유가 비어 있어도 관문 쪽으로 본다).
        거부 사유는 부분 체결·취소로 닫힌 행에도 남으므로 상태로 거르지 않는다.
        평생 누적으로 세면 석 달 전의 일시 오류 하나가 영영 실전을 막는다.
        """
        rows = self.s.scalars(select(TradeOrder).where(TradeOrder.created_at >= since))
        n = 0
        for o in rows:
            if o.status == "unknown":
                n += 1
            elif o.reject_reason and o.reject_reason not in GATE_REASONS:
                n += 1
        return n

    def check(self) -> GateResult:
        """다섯 조건을 집계한다. 월마다 재현을 한 번씩 돌리므로 몇 분 걸릴 수 있다."""
        decisions = self._decisions()
        since = (self.clock.today() - timedelta(days=MIN_PAPER_DAYS)).isoformat()
        days, rebalances, errors = self._paper_days(), len(decisions), self._order_errors(since)

        judged = [m for m in (self.replay_matches(d.asof) for d in decisions) if m is not None]
        match_rate = (sum(judged) / len(judged)) if judged else UNMEASURED

        gaps = [abs(g) for g in (self.month_gap(d) for d in decisions) if g is not None]
        gap = max(gaps) if gaps else UNMEASURED  # 하나도 못 재면 통과시키지 않는다

        conds = [
            Condition("모의 운용 기간", days, days >= MIN_PAPER_DAYS, f"{days}일 (기준 {MIN_PAPER_DAYS}일)"),
            Condition(
                "월말 교체", rebalances, rebalances >= MIN_REBALANCES, f"{rebalances}회 (기준 {MIN_REBALANCES}회)"
            ),
            Condition("주문 오류", errors, errors <= MAX_ORDER_ERRORS, f"{errors}건 (기준 {MAX_ORDER_ERRORS}건)"),
            Condition(
                "선정 일치율",
                match_rate,
                bool(judged) and match_rate >= MIN_SELECTION_MATCH,
                f"{match_rate:.0%} ({len(judged)}개월 비교, 기준 100%)" if judged else "비교할 판단이 없다(못 쟀음)",
            ),
            Condition(
                "월 수익 차이",
                gap,
                bool(gaps) and gap <= MAX_RETURN_GAP,
                f"최대 {gap:.2%} ({len(gaps)}개월, 기준 {MAX_RETURN_GAP:.0%}p 이내)"
                if gaps
                else "장부상 수익률과 비교할 자료가 없다(못 쟀음)",
            ),
        ]
        rec = GateRecord(
            checked_at=self._now(),
            paper_days=days,
            rebalances=rebalances,
            order_errors=errors,
            selection_match=match_rate,
            return_gap=gap,
            verdict="pass" if all(c.ok for c in conds) else "fail",
        )
        self.s.add(rec)
        self.s.flush()
        return GateResult(conds, rec)

    # ----- 승인 -----
    def approve(self, capital: int, first_month_ratio: float = 0.3, command_id: int | None = None) -> GateRecord:
        """마지막 통과 기록을 승인하고 실전 데이터베이스를 만든다 (QBOT-MS-001 RiskService.gate_approve).

        이 프로세스의 모의 상태는 건드리지 않는다. 승인했다고 돌고 있는 모의 운용이
        갑자기 첫 달 상한에 막히거나 손실 기준선이 지워지면 안 된다. 실전은 재시작부터다.
        """
        rec = self.s.scalars(select(GateRecord).order_by(GateRecord.id.desc())).first()
        if rec is None or rec.verdict != "pass":
            raise GateFailed("관문을 통과한 기록이 없다. gate_check 부터 돌린다")
        if rec.approved_at:
            raise Precondition("이미 승인된 관문 기록이다")
        if not self.live_ready():
            raise Precondition("실전 접속 정보(키·시크릿·계좌 8자리)가 환경 변수에 없다")
        if self.live_probe is not None:
            self.live_probe()  # 실전 계좌 접속 시험. 실패하면 예외가 올라가고 아무것도 바뀌지 않는다

        now = self._now()
        cap = int(capital * first_month_ratio)
        rec.approved_by_command_id, rec.approved_at = command_id, now
        rec.capital, rec.first_month_ratio = capital, first_month_ratio
        if self.seed_live is None:
            raise Precondition("실전 데이터베이스를 만들 수 없다(조립 오류)")
        self.seed_live(rec, cap, now)
        if self.notify:
            self.notify(
                "error",
                f"실전 전환 승인. 자금 {capital:,}원, 첫 달 상한 {cap:,}원 ({first_month_ratio:.0%}). "
                "실전 데이터베이스를 만들었다. QBOT_MODE=live 로 다시 시작해야 실전 주문이 나간다",
            )
        return rec


def seed_live_db(live_db: Path, rec: GateRecord, first_month_cap: int, now: str) -> None:
    """승인 기록을 실전 데이터베이스에 옮겨 심는다.

    모의와 실전은 파일이 다르다(QBOT-INFRA-001 6장). 모의 쪽에만 승인을 남기면
    실전으로 시작할 때 빈 파일을 열어 "관문 기록 없음"으로 영영 거부된다.
    """
    root = Path(__file__).resolve().parents[3]
    before = os.environ.get("QBOT_DB_PATH")
    os.environ["QBOT_DB_PATH"] = str(live_db)
    try:
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(cfg, "head")  # 없으면 만들고, 있으면 최신으로
    finally:
        if before is None:
            os.environ.pop("QBOT_DB_PATH", None)
        else:
            os.environ["QBOT_DB_PATH"] = before

    with Session(make_engine(live_db)) as s:
        if s.get(BotState, 1) is not None:
            raise Precondition(f"{live_db}에 이미 상태가 있다. 실전 기록을 덮어쓰지 않는다")
        copy = GateRecord(
            checked_at=rec.checked_at,
            paper_days=rec.paper_days,
            rebalances=rec.rebalances,
            order_errors=rec.order_errors,
            selection_match=rec.selection_match,
            return_gap=rec.return_gap,
            verdict=rec.verdict,
            approved_by_command_id=rec.approved_by_command_id,
            approved_at=rec.approved_at,
            capital=rec.capital,
            first_month_ratio=rec.first_month_ratio,
        )
        s.add(copy)
        s.flush()
        s.add(
            BotState(
                id=1,
                mode="live",
                gate_record_id=copy.id,
                live_since=now,
                first_month_cap=first_month_cap,
                updated_at=now,
            )
        )
        s.commit()


def refuse_live_without_gate(session: Session, mode: str) -> None:
    """설정만 실전으로 바꾼 봇은 시작을 거부한다 (UC-H2 5a).

    통과 기록은 DB에 있고, 설정 파일은 저장소 밖 환경 변수다. 둘이 맞아야 실전이 켜진다.
    """
    if mode != "live":
        return
    state = RiskCrud(session).state_or_none()
    if state is None or state.gate_record_id is None:
        raise GateFailed(
            "실전 모드인데 관문 통과 기록이 없다. gate_check → gate_approve 를 거치지 않고는 시작하지 않는다"
        )


def probe_live(balance_fn: Callable[[], object]) -> object:
    """실전 계좌 접속 시험. 잔고가 읽히지 않으면 모드를 바꾸지 않는다 (UC-H2 6a)."""
    try:
        return balance_fn()
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 전환을 막아야 한다
        raise BrokerUnavailable(f"실전 계좌에 접속할 수 없다: {e}") from e

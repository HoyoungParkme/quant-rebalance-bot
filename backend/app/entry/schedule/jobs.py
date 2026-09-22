"""스케줄 입구 (QBOT-INFRA-001 8.1). 시각마다 유스케이스를 부른다.

작업 하나가 실패해도 다음 작업은 돈다. 실패는 알림으로 알리고 기록만 남긴다.
거래일이 아닌 날에는 장 관련 작업을 건너뛴다. 달력은 수집이 매일 갱신한다.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.clock import KST

BACKUP_KEEP_DAYS = 30


@dataclass
class Jobs:
    app: object  # app.main.App. 순환 수입을 피하려고 타입을 느슨하게 둔다
    lock: threading.RLock | None = None  # 메신저 입구와 같은 세션을 쓴다. 동시에 들어가면 안 된다

    # ----- 도움 -----
    def _today(self) -> date:
        return self.app.clock.today()

    def _is_trading_day(self, d: date) -> bool:
        return self.app.md.crud.calendar_between(d.isoformat(), d.isoformat()) != []

    def _pending(self):
        """실행할 판단. 가장 최근 것을 본다.

        거래정지로 영영 partial에 머무는 판단이 하나 있으면, 오래된 것부터 집을 때
        다음 달 리밸런싱이 계속 가려진다.
        """
        rows = self.app.decision.crud.pending()
        return rows[-1] if rows else None

    def run(self, name: str, fn, trading_only: bool = True) -> None:
        """작업 하나를 감싼다. 거래일 확인, 오류 알림, 세션 정리를 여기서 한다.

        SQLAlchemy 세션은 스레드 안전하지 않고 SQLite 연결도 스레드를 넘지 못한다.
        스케줄은 별도 스레드에서 도므로 메신저 입구와 같은 자물쇠로 순서를 지킨다.
        """
        with self.lock or nullcontext():
            self._run_locked(name, fn, trading_only)

    def _run_locked(self, name: str, fn, trading_only: bool) -> None:
        try:
            if trading_only and not self._is_trading_day(self._today()):
                return
            fn()
            self.app.session.commit()
        except Exception as e:  # noqa: BLE001 - 한 작업의 실패가 스케줄 전체를 멈추면 안 된다
            self.app.session.rollback()
            try:
                self.app.ops.notify("error", f"{name} 실패: {e}")
                self.app.session.commit()
            except Exception:  # noqa: BLE001 - 알림까지 실패하면 다음 작업으로 넘어간다
                self.app.session.rollback()

    # ----- 작업 -----
    def resume_after_restart(self) -> None:
        """부팅 직후. 보냈는지 모르는 주문을 증권사 내역과 맞춘다 (UC-A4)."""
        out = self.app.trading.resume_after_restart()
        if out:
            self.app.ops.notify("error", f"재시작 정리: 미확정 주문 {len(out)}건 확인")

    def prepare(self) -> None:
        """08:20. 대기 중인 판단이 있으면 알린다. 사람이 막을 시간을 준다."""
        d = self._pending()
        if d is None:
            return
        picks = self.app.decision.crud.picks(d.id)
        self.app.ops.notify("summary", f"오늘 {d.asof} 판단을 실행한다. 매수 후보 {len(picks)}종목")

    def sell_phase(self) -> None:
        """08:40. 장 시작 전 동시호가에 매도를 걸어 둔다."""
        d = self._pending()
        if d is None:
            return
        res = self.app.trading.execute(d, phase="sell")
        if res.errors or res.skipped or res.held:
            self.app.ops.notify("error", f"{d.asof} 장 시작 전 매도: {res.summary()}")

    def buy_phase(self) -> None:
        """09:05. 매도 체결을 확인하고 그 돈으로 산다. 보류 매도도 다시 시도한다."""
        d = self._pending()
        if d is not None:
            self.app.trading.execute(d, phase="buy")  # 체결 요약 알림은 execute가 보낸다
        self.app.trading.retry_held_sells()

    def cancel_open(self) -> None:
        """15:40. 미체결을 남기지 않는다 (QBOT-INFRA-001 C13)."""
        n = self.app.trading.cancel_open()
        if n:
            self.app.ops.notify("error", f"장 마감. 미체결 {n}건 취소")

    def evening(self) -> None:
        """18:30. 수집 → 대조 → 평가액 → 요약 (QBOT-SCN-001 S1)."""
        d = self._today()
        res = self.app.md.collect_daily(d)
        if res.skipped:
            return
        for msg in res.alerts:
            self.app.ops.notify("keyword", msg)
        self.app.trading.reconcile()
        self.app.valuation.record(d)
        self.app.session.commit()
        self.app.ops.notify("summary", self.app.reporting.daily_summary(d))

    def month_end_decide(self) -> None:
        """18:50. 오늘이 월말 거래일이면 다음 달 종목을 정한다 (UC-A2)."""
        d = self._today()
        if d.isoformat() not in self.app.md.month_ends_until(d, 1):
            return
        self.app.decision.decide_month_end(d, self.app.settings.mode)

    def monthly_report(self) -> None:
        """19:00. 이번 달 첫 거래일이면 지난달 보고 (UC-A6)."""
        d = self._today()
        ends = self.app.md.month_ends_until(d, 1)
        if not ends or ends[0] >= d.isoformat():
            return  # 이번 달 첫 거래일이 아니다(직전 월말이 오늘이거나 미래)
        first = self.app.md.crud.calendar_between(d.replace(day=1).isoformat(), d.isoformat())
        if not first or first[0] != d.isoformat():
            return
        prev = d.replace(day=1) - timedelta(days=1)
        row = self.app.reporting.monthly(prev.year, prev.month, self.app.settings.mode)
        self.app.ops.notify("summary", self.app.reporting.describe_monthly(row))

    def backup(self) -> None:
        """19:30. 데이터베이스를 복사해 둔다. 판단·주문 기록은 잃으면 복구할 수 없다."""
        db = Path(self.app.settings.db_path)
        if not db.exists():
            return
        out_dir = db.parent / "backup"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{db.stem}-{self._today().isoformat()}.sqlite3"
        target.unlink(missing_ok=True)  # VACUUM INTO는 있는 파일에 쓰지 않는다
        # 파일 복사는 WAL에서 반쪽이 될 수 있고, 백업 API(backup)는 적재가 쓰는 중이면
        # 복사를 계속 다시 시작한다. VACUUM INTO는 읽기 스냅숏 하나로 끝낸다
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
        try:
            src.execute("VACUUM INTO ?", (str(target),))
        finally:
            src.close()
        cutoff = (self._today() - timedelta(days=BACKUP_KEEP_DAYS)).isoformat()
        for old in sorted(out_dir.glob(f"{db.stem}-*.sqlite3")):
            if old.stem[len(db.stem) + 1 :] < cutoff:  # 파일 이름 뒤가 날짜다
                old.unlink()

    def heartbeat(self) -> None:
        """매시 정각. 상태의 시각만 갱신하고 메시지는 하루 한 번(09시)만 보낸다.

        시간마다 보내면 하루 24통이라 정작 중요한 알림이 묻힌다. 살아 있는지는 status가 보여 준다.
        """
        self.app.risk.touch_heartbeat()
        if self.app.clock.now().hour == 9:
            self.app.ops.heartbeat()


def build_scheduler(app, jobs: Jobs | None = None) -> BackgroundScheduler:
    """스케줄 표(QBOT-INFRA-001 8.1) 그대로 등록한다. 시각은 전부 한국 시간."""
    j = jobs or Jobs(app, lock=getattr(app, "lock", None))
    s = BackgroundScheduler(
        timezone=KST,
        job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1},
        executors={"default": ThreadPoolExecutor(1)},  # 작업끼리도 겹치지 않게 한 줄로 돈다
    )
    plan = [
        ("prepare", 8, 20, lambda: j.run("주문 준비", j.prepare)),
        ("sell", 8, 40, lambda: j.run("장 시작 전 매도", j.sell_phase)),
        ("buy", 9, 5, lambda: j.run("매수", j.buy_phase)),
        ("cancel", 15, 40, lambda: j.run("미체결 취소", j.cancel_open)),
        ("evening", 18, 30, lambda: j.run("저녁 수집", j.evening, trading_only=False)),
        ("decide", 18, 50, lambda: j.run("월말 판단", j.month_end_decide)),
        ("report", 19, 0, lambda: j.run("월간 보고", j.monthly_report)),
        ("backup", 19, 30, lambda: j.run("백업", j.backup, trading_only=False)),
    ]
    for name, hour, minute, fn in plan:
        s.add_job(fn, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=KST), id=name)
    s.add_job(lambda: j.run("생존 신호", j.heartbeat, trading_only=False), CronTrigger(minute=0), id="heartbeat")
    return s

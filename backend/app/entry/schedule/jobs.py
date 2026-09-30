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
REFRESH_FAIL_LIMIT = 150  # 판단 전 재수집 실패가 이보다 많으면(약 5%) 판단을 보류한다


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
        """08:40. 장 시작 전 동시호가에 매도를 걸어 둔다. 대기 판단이 없어도 손절 예정 종목은 판다."""
        d = self._pending()
        if d is not None:
            res = self.app.trading.execute(d, phase="sell")  # 목표에서 빠진 종목과 손절 예정 종목
            if res.errors or res.skipped or res.held:
                self.app.ops.notify("error", f"{d.asof} 장 시작 전 매도: {res.summary()}")
            return
        if not self.app.trading.stop_loss_codes():
            return
        last = self.app.decision.crud.latest_real(self.app.settings.mode)
        if last is None:
            return
        res = self.app.trading.sell_stop_losses(last)
        self.app.ops.notify("order", f"손절 매도 주문: {res.summary()}")

    def buy_phase(self) -> None:
        """09:05. 매도 체결을 확인하고 그 돈으로 산다. 보류 매도도 다시 시도한다."""
        d = self._pending()
        if d is not None:
            self.app.trading.execute(d, phase="buy")  # 체결 요약 알림은 execute가 보낸다
        else:
            self.app.trading.confirm_open_orders()  # 대기 판단 없이 낸 손절 매도의 체결을 기록한다
        self.app.trading.retry_held_sells()

    def cancel_open(self) -> None:
        """15:40. 미체결을 남기지 않는다 (QBOT-INFRA-001 C13)."""
        n = self.app.trading.cancel_open()
        if n:
            self.app.ops.notify("error", f"장 마감. 미체결 {n}건 취소")

    def evening(self) -> None:
        """20:10. 수집 → 대조 → 평가액 → 요약 (QBOT-SCN-001 S1). 당일 봉이 확정되는 20:00 뒤에 돈다."""
        d = self._today()
        res = self.app.md.collect_daily(d)
        if res.skipped:
            return
        for msg in res.alerts:
            self.app.ops.notify("keyword", msg)
        if res.bars_failed:
            self.app.ops.notify("error", f"일봉 수집 실패 {len(res.bars_failed)}종목: {', '.join(res.bars_failed[:5])}")
        if res.series_bumped:
            self.app.ops.notify(
                "error", f"수정주가 새 판 {len(res.series_bumped)}종목: {', '.join(res.series_bumped[:10])}"
            )
        if res.late_changes:  # 20:00 확정 규칙이 틀렸다는 신호. 매일 여러 건이면 수집 시각을 늦춘다
            head = ", ".join(res.late_changes[:5])
            self.app.ops.notify("error", f"확정 뒤 바뀐 봉 {len(res.late_changes)}건: {head}")
        self.app.trading.reconcile()
        self.app.valuation.record(d)
        self.app.session.commit()
        marks = self.app.trading.mark_stop_losses(d, self.app.md.closes_on(d.isoformat()))
        if marks:
            lines = [f"{m['code']} 평단 {m['avg_cost']:,} → 종가 {m['close']:,} ({m['loss']:+.1%})" for m in marks]
            self.app.ops.notify("order", "내일 손절 매도 예정:\n" + "\n".join(lines))
        self.app.ops.notify("summary", self.app.reporting.daily_summary(d))

    def month_end_decide(self) -> None:
        """21:00. 오늘이 월말 거래일이면 당일 봉을 다시 받아 확정한 뒤 다음 달 종목을 정한다 (UC-A2).

        20:10 수집이 받은 당일 봉은 날에 따라 아직 바뀐다(QBOT-INFRA-001 C10). 판단 직전에 한 번 더 받는다.
        수집이 실패했거나 덜 끝났으면 판단하지 않는다 — 다음 날 07:00 month_end_retry가 다시 한다.
        """
        d = self._today()
        if d.isoformat() not in self.app.md.month_ends_until(d, 1):
            return
        changed, failed = self.app.md.refresh_bars(d)
        self.app.session.commit()
        if changed or failed:
            self.app.ops.notify(
                "error" if failed else "summary",
                f"{d} 판단 전 재수집: 20:10 뒤 바뀐 봉 {changed}건, 실패 {len(failed)}종목",
            )
        if len(failed) > REFRESH_FAIL_LIMIT:
            # 재수집이 통째로 실패했으면(증권사 장애) 20:10 잠정값으로 판단하지 않는다. 판단이 생기면 07:00 안전망도
            # 건너뛰므로, 여기서 보류해야 내일 아침에 다시 받고 판단한다 (리뷰 지적)
            self.app.ops.notify("error", f"월말 판단 보류: 재수집 실패 {len(failed)}종목. 07:00에 다시 받는다")
            return
        self._decide_if_ready(d)

    def month_end_retry(self) -> None:
        """07:00. 어제가 월말인데 판단이 없으면(수집 실패·지연) 그날을 다시 받고 판단한다.

        판단이 한 번 빠지면 그달 리밸런싱이 통째로 사라진다. 저녁 판단의 안전망이다.
        """
        d = self._today()
        prev = self.app.md.crud.calendar_between(
            (d - timedelta(days=14)).isoformat(), (d - timedelta(days=1)).isoformat()
        )
        if not prev:
            return
        asof = date.fromisoformat(prev[-1])
        if prev[-1] not in self.app.md.month_ends_until(asof, 1):
            return
        if self.app.decision.crud.real_decision(prev[-1], self.app.settings.mode) is not None:
            return
        self.app.ops.notify("error", f"{asof} 월말 판단이 없다. 그날 봉을 다시 받고 판단한다")
        self.app.md.collect_daily(asof)
        self.app.session.commit()
        self._decide_if_ready(asof)

    def _decide_if_ready(self, asof: date) -> None:
        ok, detail = self.app.md.bars_ready(asof)
        if not ok:
            self.app.ops.notify("error", f"월말 판단 보류: {detail}. 수집이 덜 끝났다")
            return
        self.app.decision.decide_month_end(asof, self.app.settings.mode)

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
        """21:20. 데이터베이스를 복사해 둔다. 판단·주문 기록은 잃으면 복구할 수 없다."""
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
        ("retry_decide", 7, 0, lambda: j.run("월말 판단 재시도", j.month_end_retry)),
        ("prepare", 8, 20, lambda: j.run("주문 준비", j.prepare)),
        ("sell", 8, 40, lambda: j.run("장 시작 전 매도", j.sell_phase)),
        ("buy", 9, 5, lambda: j.run("매수", j.buy_phase)),
        ("cancel", 15, 40, lambda: j.run("미체결 취소", j.cancel_open)),
        ("evening", 20, 10, lambda: j.run("저녁 수집", j.evening, trading_only=False)),
        ("decide", 21, 0, lambda: j.run("월말 판단", j.month_end_decide)),
        ("report", 19, 0, lambda: j.run("월간 보고", j.monthly_report)),
        ("backup", 21, 20, lambda: j.run("백업", j.backup, trading_only=False)),
    ]
    for name, hour, minute, fn in plan:
        s.add_job(fn, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=KST), id=name)
    s.add_job(lambda: j.run("생존 신호", j.heartbeat, trading_only=False), CronTrigger(minute=0), id="heartbeat")
    return s

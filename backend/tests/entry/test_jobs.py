"""스케줄 작업 (QBOT-INFRA-001 8.1). 실제 시각이 아니라 작업 함수를 직접 부른다."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

from app.core.clock import KST, FrozenClock
from app.entry.schedule.jobs import Jobs, build_scheduler

TODAY = date(2026, 9, 22)


def fake_app(session=None, trading_day=True, **kw):
    notes, calls = [], []
    md = SimpleNamespace(
        crud=SimpleNamespace(calendar_between=lambda f, t: [f] if trading_day else []),
        collect_daily=lambda d: calls.append(("collect", d)) or SimpleNamespace(skipped=False, alerts=[]),
        month_ends_until=lambda d, n: kw.get("month_ends", []),
    )
    app = SimpleNamespace(
        clock=FrozenClock(datetime(2026, 9, 22, 18, 30, tzinfo=KST)),
        session=SimpleNamespace(commit=lambda: None, rollback=lambda: calls.append(("rollback", None))),
        settings=SimpleNamespace(mode="paper", db_path=kw.get("db_path")),
        md=md,
        ops=SimpleNamespace(
            notify=lambda kind, text: notes.append((kind, text)),
            heartbeat=lambda: calls.append(("heartbeat", None)),
        ),
        risk=SimpleNamespace(touch_heartbeat=lambda: calls.append(("touch", None))),
        decision=SimpleNamespace(
            crud=SimpleNamespace(pending=lambda: kw.get("pending", []), picks=lambda i: ["000001"]),
            decide_month_end=lambda d, m: calls.append(("decide", d)),
        ),
        trading=SimpleNamespace(
            execute=lambda d, phase="all": calls.append(("execute", phase)) or SimpleNamespace(summary=lambda: "요약"),
            reconcile=lambda: calls.append(("reconcile", None)),
            retry_held_sells=lambda: calls.append(("retry", None)) or [],
            cancel_open=lambda: kw.get("cancelled", 0),
            resume_after_restart=lambda: kw.get("unknowns", []),
        ),
        valuation=SimpleNamespace(record=lambda d: calls.append(("valuation", d))),
        reporting=SimpleNamespace(
            daily_summary=lambda d: "요약 한 줄",
            monthly=lambda y, m, mode: SimpleNamespace(id=1),
            describe_monthly=lambda r: "월간 보고",
        ),
    )
    return Jobs(app), notes, calls


def test_non_trading_day_skips_market_jobs(session):
    j, notes, calls = fake_app(trading_day=False)
    j.run("매수", j.buy_phase)
    assert calls == [] and notes == []


def test_evening_runs_collect_reconcile_valuation_summary_in_order(session):
    j, notes, calls = fake_app()
    j.run("저녁", j.evening, trading_only=False)
    assert [c[0] for c in calls] == ["collect", "reconcile", "valuation"]
    assert notes[-1] == ("summary", "요약 한 줄")


def test_a_failing_job_notifies_and_does_not_raise(session):
    j, notes, calls = fake_app()

    def boom():
        raise RuntimeError("증권사 응답 없음")

    j.run("저녁 수집", boom, trading_only=False)
    assert ("rollback", None) in calls
    assert notes and "저녁 수집 실패" in notes[0][1] and "증권사 응답 없음" in notes[0][1]


def test_sell_phase_and_buy_phase_split_the_execution(session):
    pending = [SimpleNamespace(id=1, asof="2026-08-31")]
    j, notes, calls = fake_app(pending=pending)
    j.run("매도", j.sell_phase)
    j.run("매수", j.buy_phase)
    assert [c for c in calls if c[0] == "execute"] == [("execute", "sell"), ("execute", "buy")]
    assert ("retry", None) in calls  # 보류 매도도 아침에 다시 본다


def test_month_end_decide_only_on_a_month_end(session):
    j, _, calls = fake_app(month_ends=["2026-08-31"])
    j.run("판단", j.month_end_decide)
    assert calls == []
    j2, _, calls2 = fake_app(month_ends=["2026-09-22"])
    j2.run("판단", j2.month_end_decide)
    assert [c[0] for c in calls2] == ["decide"]


def test_backup_copies_the_database_and_drops_old_ones(tmp_path, session):
    db = tmp_path / "qbot-paper.sqlite3"
    con = sqlite3.connect(db)
    con.execute("create table t (a int)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    old = tmp_path / "backup" / "qbot-paper-2026-01-01.sqlite3"
    old.parent.mkdir()
    old.write_text("오래된 백업")

    j, _, _ = fake_app(db_path=db)
    j.run("백업", j.backup, trading_only=False)
    made = tmp_path / "backup" / "qbot-paper-2026-09-22.sqlite3"
    assert made.exists() and not old.exists()  # 30일보다 오래된 것은 지운다
    assert sqlite3.connect(made).execute("select a from t").fetchone() == (1,)


def test_scheduler_registers_every_job_in_the_table(session):
    j, _, _ = fake_app()
    s = build_scheduler(j.app, j)
    ids = {job.id for job in s.get_jobs()}
    assert ids == {"prepare", "sell", "buy", "cancel", "evening", "decide", "report", "backup", "heartbeat"}
    trig = {job.id: str(job.trigger) for job in s.get_jobs()}
    assert "hour='8'" in trig["sell"] and "minute='40'" in trig["sell"]  # 장 시작 전 동시호가
    assert "hour='18'" in trig["evening"] and "minute='30'" in trig["evening"]
    assert str(s.get_job("evening").trigger.timezone) == "Asia/Seoul"  # 시각은 한국 시간


def test_heartbeat_touches_state_hourly_but_messages_once_a_day(session):
    j, notes, calls = fake_app()
    j.app.clock = FrozenClock(datetime(2026, 9, 22, 14, 0, tzinfo=KST))
    j.run("생존", j.heartbeat, trading_only=False)
    assert ("touch", None) in calls and notes == []  # 14시에는 기록만
    j.app.clock = FrozenClock(datetime(2026, 9, 22, 9, 0, tzinfo=KST))
    j.run("생존", j.heartbeat, trading_only=False)
    assert ("heartbeat", None) in calls  # 09시에 한 번 보낸다

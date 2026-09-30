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
        crud=SimpleNamespace(
            calendar_between=lambda f, t: (
                [x for x in kw["days"] if f <= x <= t] if "days" in kw else ([f] if trading_day else [])
            )
        ),
        collect_daily=lambda d: (
            calls.append(("collect", d))
            or SimpleNamespace(skipped=False, alerts=[], series_bumped=[], late_changes=[], bars_failed=[])
        ),
        month_ends_until=lambda d, n: [m for m in kw.get("month_ends", []) if m <= d.isoformat()][-n:],
        bars_ready=lambda d: (kw.get("ready", True), "확정 봉 0개 / 직전 거래일 3,500개"),
        refresh_bars=lambda d: calls.append(("refresh", d)) or kw.get("refreshed", (0, [])),
        closes_on=lambda day: kw.get("closes", {}),
    )
    app = SimpleNamespace(
        clock=FrozenClock(kw.get("now", datetime(2026, 9, 22, 18, 30, tzinfo=KST))),
        session=SimpleNamespace(commit=lambda: None, rollback=lambda: calls.append(("rollback", None))),
        settings=SimpleNamespace(mode="paper", db_path=kw.get("db_path")),
        md=md,
        ops=SimpleNamespace(
            notify=lambda kind, text: notes.append((kind, text)),
            heartbeat=lambda: calls.append(("heartbeat", None)),
        ),
        risk=SimpleNamespace(touch_heartbeat=lambda: calls.append(("touch", None))),
        decision=SimpleNamespace(
            crud=SimpleNamespace(
                pending=lambda: kw.get("pending", []),
                picks=lambda i: ["000001"],
                real_decision=lambda asof, mode: kw.get("existing"),
                latest_real=lambda mode: kw.get("latest"),
            ),
            decide_month_end=lambda d, m: calls.append(("decide", d)),
        ),
        trading=SimpleNamespace(
            execute=lambda d, phase="all": calls.append(("execute", phase)) or SimpleNamespace(summary=lambda: "요약"),
            reconcile=lambda: calls.append(("reconcile", None)),
            retry_held_sells=lambda: calls.append(("retry", None)) or [],
            cancel_open=lambda: kw.get("cancelled", 0),
            resume_after_restart=lambda: kw.get("unknowns", []),
            mark_stop_losses=lambda d, closes: calls.append(("stops", d)) or kw.get("marks", []),
            stop_loss_codes=lambda: kw.get("stop_codes", set()),
            sell_stop_losses=lambda dec: (
                calls.append(("stop_sell", dec)) or SimpleNamespace(summary=lambda: "손절 요약")
            ),
            confirm_open_orders=lambda *a: calls.append(("confirm", None)) or [],
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
    assert [c[0] for c in calls] == ["collect", "reconcile", "valuation", "stops"]  # 손절 표시는 평가액 뒤
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
    """20:40, 오늘이 월말 거래일일 때만. 20:10 수집이 받은 오늘 봉은 확정값이다."""
    evening = datetime(2026, 9, 30, 20, 40, tzinfo=KST)
    j, _, calls = fake_app(month_ends=["2026-08-31"], now=evening)
    j.run("판단", j.month_end_decide)
    assert calls == []
    j2, _, calls2 = fake_app(month_ends=["2026-09-30"], now=evening)
    j2.run("판단", j2.month_end_decide)
    assert calls2 == [("refresh", date(2026, 9, 30)), ("decide", date(2026, 9, 30))]  # 판단 전 당일 봉 재수집


def test_month_end_decide_reports_bars_that_changed_since_the_evening_collect(session):
    """20:10 값이 21:00에 바뀌었으면 알린다. 9/28에 60%가 바뀐 적이 있다(INFRA C10)."""
    evening = datetime(2026, 9, 30, 21, 0, tzinfo=KST)
    j, notes, calls = fake_app(month_ends=["2026-09-30"], now=evening, refreshed=(2075, []))
    j.run("판단", j.month_end_decide)
    assert ("decide", date(2026, 9, 30)) in calls and any("바뀐 봉 2075건" in t for _, t in notes)


def test_month_end_decide_waits_when_collection_did_not_finish(session):
    """수집이 실패·지연되면 20:40에 판단하지 않고 알린다. 잠정·빠진 봉으로 고르면 재현이 안 된다."""
    j, notes, calls = fake_app(month_ends=["2026-09-30"], now=datetime(2026, 9, 30, 21, 0, tzinfo=KST), ready=False)
    j.run("판단", j.month_end_decide)
    assert [c[0] for c in calls] == ["refresh"] and any(
        "월말 판단 보류" in t for _, t in notes
    )  # 재수집은 하되 판단은 안 함


def test_morning_retry_recollects_and_decides_a_missed_month_end(session):
    days = ["2026-09-30", "2026-10-01"]
    j, notes, calls = fake_app(days=days, month_ends=["2026-09-30"], now=datetime(2026, 10, 1, 7, 0, tzinfo=KST))
    j.run("재시도", j.month_end_retry)
    assert calls == [("collect", date(2026, 9, 30)), ("decide", date(2026, 9, 30))]
    assert any("월말 판단이 없다" in t for _, t in notes)


def test_morning_retry_does_nothing_when_the_decision_exists(session):
    days = ["2026-09-30", "2026-10-01"]
    j, _, calls = fake_app(
        days=days, month_ends=["2026-09-30"], now=datetime(2026, 10, 1, 7, 0, tzinfo=KST), existing=object()
    )
    j.run("재시도", j.month_end_retry)
    assert calls == []


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
    assert ids == {
        "retry_decide",
        "prepare",
        "sell",
        "buy",
        "cancel",
        "evening",
        "decide",
        "report",
        "backup",
        "heartbeat",
    }
    trig = {job.id: str(job.trigger) for job in s.get_jobs()}
    assert "hour='8'" in trig["sell"] and "minute='40'" in trig["sell"]  # 장 시작 전 동시호가
    assert "hour='20'" in trig["evening"] and "minute='10'" in trig["evening"]  # 당일 봉은 20:00에 확정
    assert "hour='21'" in trig["decide"] and "minute='0'" in trig["decide"]  # 판단 전 재수집이 있어 20:10보다 늦게
    assert str(s.get_job("evening").trigger.timezone) == "Asia/Seoul"  # 시각은 한국 시간


def test_heartbeat_touches_state_hourly_but_messages_once_a_day(session):
    j, notes, calls = fake_app()
    j.app.clock = FrozenClock(datetime(2026, 9, 22, 14, 0, tzinfo=KST))
    j.run("생존", j.heartbeat, trading_only=False)
    assert ("touch", None) in calls and notes == []  # 14시에는 기록만
    j.app.clock = FrozenClock(datetime(2026, 9, 22, 9, 0, tzinfo=KST))
    j.run("생존", j.heartbeat, trading_only=False)
    assert ("heartbeat", None) in calls  # 09시에 한 번 보낸다


def test_month_end_decide_holds_when_the_refresh_mostly_fails(session):
    """21:00 재수집이 통째로 실패하면 20:10 잠정값으로 판단하지 않는다.

    판단이 한 번 생기면 07:00 안전망이 건너뛰기 때문이다.
    """
    evening = datetime(2026, 9, 30, 21, 0, tzinfo=KST)
    j, notes, calls = fake_app(
        month_ends=["2026-09-30"], now=evening, refreshed=(0, [f"{i:06d}: 시간 초과" for i in range(200)])
    )
    j.run("판단", j.month_end_decide)
    assert [c[0] for c in calls] == ["refresh"] and any("재수집 실패 200종목" in t for _, t in notes)


def test_evening_marks_stop_losses_and_announces_them(session):
    marks = [{"code": "000001", "avg_cost": 10000, "close": 8400, "loss": -0.16}]
    j, notes, calls = fake_app(marks=marks)
    j.run("저녁", j.evening, trading_only=False)
    assert ("stops", TODAY) in calls
    assert any("내일 손절 매도 예정" in t and "000001" in t and "-16.0%" in t for _, t in notes)


def test_sell_phase_sells_stop_losses_even_without_a_pending_decision(session):
    """대기 판단이 없는 날도 08:40에 손절 예정 종목을 판다. 주문은 가장 최근 실제 판단에 매단다."""
    last = SimpleNamespace(id=7, asof="2026-09-30")
    j, notes, calls = fake_app(stop_codes={"000001"}, latest=last)
    j.run("매도", j.sell_phase)
    assert ("stop_sell", last) in calls and any("손절 매도 주문" in t for _, t in notes)
    j2, _, calls2 = fake_app(stop_codes=set(), latest=last)
    j2.run("매도", j2.sell_phase)
    assert calls2 == []  # 손절 예정이 없으면 아무것도 하지 않는다


def test_buy_phase_confirms_stop_loss_fills_without_a_pending_decision(session):
    j, _, calls = fake_app()
    j.run("매수", j.buy_phase)
    assert ("confirm", None) in calls and ("retry", None) in calls

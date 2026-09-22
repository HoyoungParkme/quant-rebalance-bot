"""실전 전환 관문 (QBOT-PRD-001 R8, QBOT-UC-001 UC-H2).

관문의 일은 하나다. 확인되지 않은 봇이 실제 돈을 움직이지 못하게 하는 것.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core.clock import KST, FrozenClock
from app.core.errors import BrokerUnavailable, GateFailed, Precondition
from app.domains.decision.models import Decision, StrategyConfig
from app.domains.risk.gate import LiveGate, probe_live, refuse_live_without_gate
from app.domains.risk.models import BotState
from app.domains.trading.models import TradeOrder

TODAY = date(2026, 9, 22)
CLOCK = FrozenClock(datetime(2026, 9, 22, 18, 0, tzinfo=KST))


def seed(session, months=4, errors=0, mode="paper"):
    session.add(BotState(id=1, mode=mode, updated_at="2026-09-22T09:00:00+09:00"))
    session.add(
        StrategyConfig(
            id=1,
            factors_json="{}",
            min_price=1000,
            min_amount20=0,
            min_mcap=0,
            n_holdings=10,
            trend_filter=0,
            index_weight=0.2,
            effective_from="2019-01-01",
        )
    )
    for i in range(months):
        asof = (TODAY.replace(day=1) - timedelta(days=30 * (months - i))).isoformat()
        session.add(
            Decision(
                asof=asof,
                mode="paper",
                strategy_config_id=1,
                code_version="t",
                status="done",
            )
        )
    for i in range(errors):
        session.add(
            TradeOrder(
                idem_key=f"e{i}",
                decision_id=1,
                instrument_id=1,
                side="buy",
                qty=1,
                order_type="market",
                status="rejected",
                reject_reason="40240000 주문가능금액 부족",
            )
        )
    session.commit()


def gate(session, matches=True, gap=0.002, live=True, probe=None, notes=None, seeded=None):
    return LiveGate(
        session,
        CLOCK,
        "paper",
        replay_matches=lambda asof: matches,
        month_gap=lambda d: gap,
        live_ready=lambda: live,
        live_probe=probe,
        seed_live=(lambda rec, cap, now: seeded.append((rec.capital, cap, now))) if seeded is not None else None,
        notify=(lambda k, t: notes.append((k, t))) if notes is not None else None,
    )


def test_a_bot_that_meets_everything_passes(session):
    seed(session)
    res = gate(session).check()
    assert res.passed and res.record.verdict == "pass"
    assert "gate_approve" in res.describe()


def test_a_single_order_error_blocks_the_gate(session):
    """주문 오류 기준은 0건이다. 한 건이면 막는다 (QBOT-SCN-001 S8 변형)."""
    seed(session, errors=1)
    res = gate(session).check()
    assert not res.passed
    assert [c.name for c in res.conditions if not c.ok] == ["주문 오류"]


def test_gate_refusals_are_not_order_errors(session):
    """관문이 막은 주문은 봇이 제대로 일한 것이다. 오류로 세면 영영 실전에 못 간다."""
    seed(session)
    session.add(
        TradeOrder(
            idem_key="g1",
            decision_id=1,
            instrument_id=1,
            side="buy",
            qty=1,
            order_type="market",
            status="rejected",
            reject_reason="concentration",
        )
    )
    session.commit()
    assert gate(session).check().passed


def test_unknown_order_counts_as_an_error(session):
    """보냈는지 모르는 주문이 남아 있으면 실전에 갈 수 없다."""
    seed(session)
    session.add(
        TradeOrder(
            idem_key="u1", decision_id=1, instrument_id=1, side="buy", qty=1, order_type="market", status="unknown"
        )
    )
    session.commit()
    assert not gate(session).check().passed


def test_short_paper_run_and_few_rebalances_block(session):
    seed(session, months=1)
    res = gate(session).check()
    failed = [c.name for c in res.conditions if not c.ok]
    assert "모의 운용 기간" in failed and "월말 교체" in failed


def test_selection_mismatch_blocks(session):
    seed(session)
    assert not gate(session, matches=False).check().passed


def test_unmeasurable_conditions_block_rather_than_pass(session):
    """비교할 자료가 없으면 '통과'가 아니라 '모른다'다. 모르면 못 간다."""
    seed(session)
    g = gate(session)
    g.replay_matches = lambda asof: None
    g.month_gap = lambda d: None
    res = g.check()
    failed = [c.name for c in res.conditions if not c.ok]
    assert "선정 일치율" in failed and "월 수익 차이" in failed


def test_return_gap_over_one_point_blocks(session):
    seed(session)
    assert not gate(session, gap=0.015).check().passed
    assert gate(session, gap=-0.009).check().passed  # 부호가 아니라 크기로 본다


def test_approve_records_and_seeds_live_without_touching_the_paper_run(session):
    """승인은 실전 DB를 만든다. 돌고 있는 모의 운용의 상태는 건드리지 않는다.

    건드리면 재시작 전까지 모의가 첫 달 상한에 막히고 손실 기준선이 지워진다.
    """
    notes, seeded = [], []
    seed(session)
    g = gate(session, notes=notes, seeded=seeded)
    g.check()
    session.commit()
    rec = g.approve(3_000_000, 0.3, command_id=5)
    assert rec.approved_at and rec.capital == 3_000_000 and rec.approved_by_command_id == 5
    assert seeded == [(3_000_000, 900_000, rec.approved_at)]
    paper = g.crud.state_or_none()
    assert paper.mode == "paper" and paper.first_month_cap is None and paper.gate_record_id is None
    assert notes and "실전" in notes[0][1]


def test_approve_without_a_pass_is_refused(session):
    seed(session, months=1)
    g = gate(session)
    g.check()
    session.commit()
    with pytest.raises(GateFailed):
        g.approve(3_000_000)


def test_approve_twice_is_refused(session):
    seed(session)
    g = gate(session, seeded=[])
    g.check()
    session.commit()
    g.approve(3_000_000)
    with pytest.raises(Precondition):
        g.approve(3_000_000)


def test_approve_without_live_keys_is_refused(session):
    seed(session)
    g = gate(session, live=False)
    g.check()
    session.commit()
    with pytest.raises(Precondition):
        g.approve(3_000_000)
    assert g.crud.state_or_none().mode == "paper"


def test_broker_failure_leaves_the_mode_alone(session):
    """실전 계좌에 접속도 못 하는데 모드만 바꾸면 안 된다 (UC-H2 6a)."""
    seed(session)

    def boom():
        raise TimeoutError("응답 없음")

    g = gate(session, probe=lambda: probe_live(boom))
    g.check()
    session.commit()
    with pytest.raises(BrokerUnavailable):
        g.approve(3_000_000)
    assert g.crud.state_or_none().mode == "paper"


def test_live_mode_without_a_gate_record_refuses_to_start(session):
    """설정 파일만 실전으로 바꿔도 켜지지 않는다 (UC-H2 5a)."""
    seed(session, mode="live")
    with pytest.raises(GateFailed):
        refuse_live_without_gate(session, "live")
    refuse_live_without_gate(session, "paper")  # 모의는 그냥 통과


def test_seeding_makes_a_live_database_that_actually_starts(tmp_path, session):
    """모의와 실전은 파일이 다르다. 승인을 모의 쪽에만 남기면 실전은 영영 시작하지 못한다."""
    from sqlalchemy.orm import Session as OrmSession

    from app.core.db import make_engine
    from app.domains.risk.gate import seed_live_db

    seed(session)
    g = gate(session, seeded=[])
    g.check()
    session.commit()
    rec = g.approve(3_000_000, 0.3)

    live_db = tmp_path / "qbot-live.sqlite3"
    seed_live_db(live_db, rec, 900_000, rec.approved_at)
    with OrmSession(make_engine(live_db)) as live:
        refuse_live_without_gate(live, "live")  # 예외가 나지 않는다
        st = live.get(BotState, 1)
        assert st.mode == "live" and st.first_month_cap == 900_000 and st.gate_record_id is not None
    with OrmSession(make_engine(live_db)) as live2, pytest.raises(Precondition):
        seed_live_db(live_db, rec, 900_000, rec.approved_at)  # 실전 기록을 덮어쓰지 않는다
        live2.close()


def test_a_replay_row_does_not_extend_the_paper_run(session):
    """2019년 날짜로 재현 한 번 돌리고 '석 달 돌렸다'가 되면 안 된다."""
    seed(session, months=1)
    session.add(Decision(asof="2019-01-31", mode="paper", strategy_config_id=1, code_version="t", status="replay"))
    session.commit()
    res = gate(session).check()
    assert res.record.paper_days < 90 and not res.passed

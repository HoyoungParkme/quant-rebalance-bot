"""D2 테스트용 조립: 종목·판단·상태를 넣고 TradingService를 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.clock import KST, FrozenClock
from app.domains.decision.models import Decision, StrategyConfig
from app.domains.decision.service import ExecutionPlan
from app.domains.marketdata.models import Instrument
from app.domains.risk.models import BotState
from app.domains.risk.service import OrderGate
from app.domains.trading.service import TradingService
from tests.domains.trading.fakes import PRICE, FakeOrderBroker

CLOCK = FrozenClock(datetime(2026, 9, 23, 9, 5, tzinfo=KST))
CODES = ["000001", "000002", "000003", "069500"]


@dataclass
class World:
    svc: TradingService
    broker: FakeOrderBroker
    decision: Decision
    session: object

    def qty(self, code: str) -> int:
        p = self.svc.crud.position(self.svc.instrument_ids()[code])
        return p.qty if p else 0


def build(
    session,
    holdings: dict[str, int] | None = None,
    cash: int = 0,
    picks: list[str] | None = None,
    index_weight: float = 0.0,
    n_holdings: int = 2,
    index_rebalance: bool = False,
    halted: set[str] | None = None,
    broker: FakeOrderBroker | None = None,
    **kw,
) -> World:
    for i, code in enumerate(CODES, start=1):
        session.add(Instrument(id=i, code=code, name=f"종목{i}", market="KOSPI", kind="common"))
    session.add(BotState(id=1, mode="paper", updated_at="2026-09-23T09:00:00+09:00"))
    session.add(
        StrategyConfig(
            id=1,
            factors_json="{}",
            min_price=1000,
            min_amount20=0,
            min_mcap=0,
            n_holdings=n_holdings,
            trend_filter=0,
            index_weight=index_weight,
            effective_from="2019-01-01",
        )
    )
    d = Decision(
        id=1,
        asof="2026-08-31",
        mode="paper",
        strategy_config_id=1,
        code_version="test",
        status="pending",
        index_rebalance=int(index_rebalance),
    )
    session.add(d)
    session.commit()

    broker = broker or FakeOrderBroker(cash=cash)
    svc = TradingService(
        session,
        broker,
        OrderGate(session, max_position_weight=1.0, daily_order_cap_multiple=100.0),
        CLOCK,
        lambda: {c: i for i, c in enumerate(CODES, start=1)},
        mode="paper",
        plan_fn=lambda _d: ExecutionPlan(
            picks=list(picks or []),
            index_weight=index_weight,
            n_holdings=n_holdings,
            index_rebalance=index_rebalance,
            cash_switch=False,
        ),
        price_fn=lambda code: PRICE,
        halted_codes_fn=lambda: set(halted or ()),
        sleep=lambda _: None,
        max_polls=2,
        **kw,
    )
    for code, q in (holdings or {}).items():
        broker.hold(code, q)
        svc.crud.upsert_position(
            svc.instrument_ids()[code], q, PRICE, "2026-08-31T15:30:00+09:00", "bot", first_on="2026-08-31"
        )
    session.commit()
    return World(svc, broker, d, session)

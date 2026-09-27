"""엔진 A: 봇 실코드 일 단위 시뮬레이션 (11 전수 테스트).

실행: <봇 파이썬> bot_sim.py <이름> <추세필터 0|1> [시작일] [끝일]
  예) backend/.venv/bin/python bot_sim.py trend 1

운영 DB가 아니라 백업 사본(~/.qbot/research-data/fulltest/base.sqlite3)을 복사해서 쓴다.
운영 기록(판단·주문·체결·평가액·대조)은 비우고, 전략 설정은 하나만 남긴다.

거래일마다 봇의 스케줄 작업(`app.entry.schedule.jobs.Jobs`)을 실제 시각 순서대로 부른다.
  08:40 sell_phase → 09:05 buy_phase → 15:40 cancel_open → 20:10 evening → 20:40 month_end_decide
판단·주문·관문·대조·평가액은 전부 봇 코드 그대로다. 바꾼 것은 셋뿐이다.
  - 시계: 그날 그 시각으로 고정한 시계(MutableClock)
  - 증권사: 가짜 증권사(FakeBroker). 시장가를 그날 시가에 체결, 체결오차·수수료·세금은 06 연구와 같은 표
  - 수집: 저녁 수집(collect_daily)은 건너뛴다. 사본 DB에 이미 과거 봉·공시가 다 있다
  주문 체결 확인의 대기(sleep)는 0초로 바꿨다(가짜 증권사는 즉시 체결).
FIX=1이면 09:05 매수 전에 confirm_open_orders()를 먼저 부른다. 봇의 execute(phase="buy")는 대조를
체결 확인보다 먼저 해서, 08:40 동시호가 매도가 체결된 계좌를 "불일치"로 보고 주문을 멈춘다(이 시험에서 발견).
결과: ~/.qbot/research-data/fulltest/<이름>/ 아래 equity.csv, events.csv, recon.csv, decisions.csv
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))

import pandas as pd  # noqa: E402

import app.core.models_registry  # noqa: E402,F401 - 모든 테이블 등록
from app.core.clock import KST, Clock  # noqa: E402
from app.core.db import make_engine, make_session_factory  # noqa: E402
from app.core.errors import BrokerRejected  # noqa: E402
from app.domains.decision.crud import DecisionCrud  # noqa: E402
from app.domains.decision.service import DecisionService, Portfolio, plan_of  # noqa: E402
from app.domains.marketdata.service import MarketDataService  # noqa: E402
from app.domains.risk.service import OrderGate, RiskService, ValuationRecorder  # noqa: E402
from app.domains.trading.ports import Balance, BrokerOrder, Holding, OrderRequest  # noqa: E402
from app.domains.trading.service import INDEX_ETF_CODE, TradingService  # noqa: E402
from app.entry.schedule.jobs import Jobs  # noqa: E402
from app.main import index_cap  # noqa: E402

ROOT = Path.home() / ".qbot/research-data/fulltest"
FEE, SLIP = 0.00015, 0.002
TAX_BY_YEAR = {2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018, 2025: 0.0015, 2026: 0.0020}
CAPITAL = 10_000_000
FIX = __import__("os").environ.get("FIX") == "1"  # 봇 버그(대조가 체결 확인보다 먼저)를 우회한 변형


class MutableClock(Clock):
    def __init__(self) -> None:
        self._at = datetime(2023, 1, 2, 9, 0, tzinfo=KST)

    def set(self, d: date, hh: int, mm: int) -> None:
        self._at = datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)

    def now(self) -> datetime:
        return self._at


# ---------------- 가짜 증권사 ----------------
@dataclass
class FakeBroker:
    O: pd.DataFrame  # 시가 (날짜 × 코드)
    C: pd.DataFrame  # 종가
    V: pd.DataFrame  # 거래량
    cash: float = CAPITAL
    hold: dict = field(default_factory=dict)  # code -> [qty, avg_cost]
    orders: dict = field(default_factory=dict)
    today: pd.Timestamp | None = None
    phase: str = "open"  # open: 장중(시가로 평가·체결), close: 장 마감 뒤(종가로 평가)
    seq: int = 0
    log: list = field(default_factory=list)
    cost: float = 0.0  # 수수료+세금+체결오차 합
    traded: float = 0.0  # 매매 금액 합(회전율용)

    def set_day(self, d: pd.Timestamp, phase: str) -> None:
        if self.today is None or d != self.today:
            self.orders = {}  # 증권사 주문 목록은 날마다 새로
        self.today, self.phase = d, phase

    def _px(self, code: str, which: str) -> float | None:
        tbl = self.O if which == "open" else self.C
        if code not in tbl.columns:
            return None
        v = tbl.at[self.today, code] if self.today in tbl.index else None
        return float(v) if v is not None and pd.notna(v) and v > 0 else None

    def last_close(self, code: str) -> float:
        if code not in self.C.columns:
            return 0.0
        s = self.C[code].loc[: self.today].dropna()
        return float(s.iloc[-1]) if len(s) else 0.0

    def mark(self, code: str) -> float:
        p = self._px(code, "open" if self.phase == "open" else "close")
        return p if p is not None else self.last_close(code)

    def tradable(self, code: str) -> bool:
        if code not in self.V.columns or self.today not in self.V.index:
            return False
        v = self.V.at[self.today, code]
        return pd.notna(v) and v > 0 and self._px(code, "open") is not None

    # OrderBrokerPort
    def place_order(self, req: OrderRequest) -> str:
        self.seq += 1
        no = str(100000 + self.seq)
        d = self.today
        filled, avg = 0, 0
        if self.tradable(req.code):
            o = self._px(req.code, "open")
            tax = TAX_BY_YEAR.get(d.year, 0.002)
            if req.side == "buy":
                px = o * (1 + SLIP)
                need = req.qty * px * (1 + FEE)
                if need > self.cash + 1e-6:
                    self.log.append((str(d.date()), "reject_cash", req.code, req.qty, round(need), round(self.cash)))
                    raise BrokerRejected("40240000 주문가능금액이 부족합니다(가짜 증권사)")
                self.cash -= need
                q0, c0 = self.hold.get(req.code, [0, 0.0])
                self.hold[req.code] = [q0 + req.qty, (q0 * c0 + req.qty * px) / (q0 + req.qty)]
                self.cost += req.qty * px * FEE + req.qty * o * SLIP
            else:
                q0, c0 = self.hold.get(req.code, [0, 0.0])
                q = min(req.qty, q0)
                px = o * (1 - SLIP)
                gross = q * px
                self.cash += gross - gross * (FEE + tax)
                self.cost += gross * (FEE + tax) + q * o * SLIP
                if q0 - q > 0:
                    self.hold[req.code] = [q0 - q, c0]
                else:
                    self.hold.pop(req.code, None)
            filled, avg = (req.qty if req.side == "buy" else min(req.qty, q0)), round(px)
            self.traded += filled * px
        else:
            self.log.append((str(d.date()), "no_trade_today", req.code, req.side, req.qty))
        self.orders[no] = BrokerOrder(no, req.code, req.side, req.qty, filled, avg, False, raw_no=no)
        return no

    def cancel_order(self, order_no: str) -> bool:
        o = self.orders.get(order_no)
        if o is None or o.cancelled or o.filled_qty >= o.qty:
            return False
        self.orders[order_no] = BrokerOrder(
            o.order_no, o.code, o.side, o.qty, o.filled_qty, o.avg_price, True, raw_no=o.raw_no
        )
        return True

    def order_status(self, order_no: str) -> BrokerOrder | None:
        return self.orders.get(order_no)

    def orders_today(self) -> list[BrokerOrder]:
        return list(self.orders.values())

    def balance(self) -> Balance:
        hs = {}
        for c, (q, ac) in self.hold.items():
            hs[c] = Holding(c, int(q), int(round(ac)), int(round(q * self.mark(c))))
        total = int(round(self.cash + sum(h.value for h in hs.values())))
        return Balance(int(round(self.cash)), total, hs)


# ---------------- 사본 DB 준비 ----------------
def prepare_db(name: str, trend: int) -> Path:
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    db = out / "sim.sqlite3"
    if db.exists():
        db.unlink()
    for ext in ("-wal", "-shm"):
        Path(str(db) + ext).unlink(missing_ok=True)
    shutil.copy(ROOT / "base.sqlite3", db)
    c = sqlite3.connect(db)
    for t in [
        "alert", "command", "score", "decision", "fill", "position", "reconciliation_diff", "reconciliation",
        "trade_order", "valuation", "gate_record", "monthly_report", "rule_review",
    ]:
        c.execute(f"delete from {t}")
    base = c.execute("select factors_json,min_price,min_amount20,min_mcap,n_holdings,index_weight from strategy_config where id=1").fetchone()
    c.execute("delete from strategy_config")
    c.execute(
        "insert into strategy_config(factors_json,min_price,min_amount20,min_mcap,n_holdings,trend_filter,index_weight,"
        "effective_from,created_at) values (?,?,?,?,?,?,?, '2019-01-01', '2019-01-01T00:00:00+09:00')",
        (*base[:5], trend, base[5]),
    )
    c.execute(
        "update bot_state set halted=0,buy_suspended=0,orders_blocked=0,peak_equity=0,principal=0,peak_reset_at=NULL,"
        "live_since=NULL,gate_record_id=NULL,first_month_cap=NULL"
    )
    c.commit()
    c.close()
    return db


def load_prices(db: Path, frm: str, to: str):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql(
        "select i.code, b.trade_date, b.open, b.close, b.volume from daily_bar b join instrument i on i.id=b.instrument_id "
        "where b.series_no=1 and b.trade_date between ? and ?",
        c,
        params=(frm, to),
        parse_dates=["trade_date"],
    )
    c.close()
    piv = {k: df.pivot(index="trade_date", columns="code", values=k) for k in ("open", "close", "volume")}
    return piv["open"], piv["close"], piv["volume"]


# ---------------- 조립 ----------------
def assemble(db: Path, broker: FakeBroker, clock: MutableClock):
    session = make_session_factory(make_engine(db))()
    md = MarketDataService(session, broker=None, filings=None, clock=clock)
    notes: list[tuple[str, str, str]] = []

    def notify(kind: str, text: str) -> None:
        notes.append((clock.now().isoformat(timespec="minutes"), kind, text))

    def price_fn(code: str) -> int:
        p = broker._px(code, "open")
        return int(p) if p else 0

    trading = TradingService(
        session,
        broker,
        OrderGate(
            session, 0.15, 2.0, index_etf=INDEX_ETF_CODE, index_cap_fn=lambda: index_cap(DecisionCrud(session), clock.today())
        ),
        clock,
        md.instrument_ids,
        mode="paper",
        plan_fn=lambda d: plan_of(DecisionCrud(session), d),
        state_fn=lambda: risk.state_dict(),
        ensure_instrument_fn=md.ensure_instrument,
        price_fn=price_fn,
        halted_codes_fn=lambda: set(),
        block_orders_fn=lambda reason: risk.block_orders(reason),
        unblock_orders_fn=lambda: risk.unblock_orders(),
        notify=notify,
        sleep=lambda s: None,
    )
    risk = RiskService(session, clock, "paper", cancel_open_orders=trading.cancel_open)
    valuation = ValuationRecorder(risk, trading.balance, flow_fn=trading.crud.accepted_flow_on, notify=notify)

    def portfolio() -> Portfolio:
        bal = broker.balance()
        idx = bal.holdings.get(INDEX_ETF_CODE)
        return Portfolio(total=bal.total_equity, index_value=idx.value if idx else 0)

    decision = DecisionService(session, md, portfolio, "sim", notifier=notify)
    md.collect_daily = lambda d: SimpleNamespace(  # 수집은 건너뛴다(사본에 이미 있다)
        skipped=False, alerts=[], series_bumped=[], late_changes=[], bars_failed=[]
    )
    app = SimpleNamespace(
        clock=clock,
        session=session,
        settings=SimpleNamespace(mode="paper", db_path=str(db)),
        md=md,
        ops=SimpleNamespace(notify=notify, heartbeat=lambda: None),
        risk=risk,
        decision=decision,
        trading=trading,
        valuation=valuation,
        reporting=SimpleNamespace(daily_summary=lambda d: ""),
    )
    return Jobs(app), app, notes


def main() -> None:
    name, trend = sys.argv[1], int(sys.argv[2])
    start = sys.argv[3] if len(sys.argv) > 3 else "2023-04-01"
    end = sys.argv[4] if len(sys.argv) > 4 else "2026-09-23"
    db = prepare_db(name, trend)
    O, C, V = load_prices(db, "2022-06-01", end)
    broker = FakeBroker(O, C, V)
    clock = MutableClock()
    jobs, app, notes = assemble(db, broker, clock)
    days = [pd.Timestamp(x) for x in app.md.crud.calendar_between(start, end)]
    out = ROOT / name
    eq, recon = [], []
    t0 = time.time()
    for k, d in enumerate(days):
        dd = d.date()
        broker.set_day(d, "open")
        clock.set(dd, 8, 40)
        jobs.run("매도", jobs.sell_phase)
        clock.set(dd, 9, 5)
        if FIX:  # 수정 가정: 09:05에 동시호가 매도 체결을 먼저 확인하고 나서 대조한다(봇은 대조가 먼저다)
            jobs.run("체결 확인", lambda: app.trading.confirm_open_orders())
        jobs.run("매수", jobs.buy_phase)
        broker.set_day(d, "close")
        clock.set(dd, 15, 40)
        jobs.run("미체결 취소", jobs.cancel_open)
        clock.set(dd, 20, 10)
        jobs.run("저녁", jobs.evening, trading_only=False)
        r = app.trading.crud.s.execute(
            __import__("sqlalchemy").text("select result, bot_cash, broker_cash from reconciliation order by id desc limit 1")
        ).first()
        recon.append((str(dd), *(r or (None, None, None))))
        clock.set(dd, 20, 40)
        jobs.run("월말 판단", jobs.month_end_decide)
        bal = broker.balance()
        idx = bal.holdings.get(INDEX_ETF_CODE)
        eq.append((str(dd), bal.total_equity, bal.cash, idx.value if idx else 0, len(bal.holdings), broker.cost, broker.traded))
        if k % 20 == 0:
            print(f"{name} {dd} 평가액 {bal.total_equity:,} 보유 {len(bal.holdings)} ({time.time() - t0:.0f}s)", flush=True)
            pd.DataFrame(eq, columns=["date", "equity", "cash", "etf", "n", "cost", "traded"]).to_csv(out / "equity.csv", index=False)
    pd.DataFrame(eq, columns=["date", "equity", "cash", "etf", "n", "cost", "traded"]).to_csv(out / "equity.csv", index=False)
    pd.DataFrame(recon, columns=["date", "result", "bot_cash", "broker_cash"]).to_csv(out / "recon.csv", index=False)
    pd.DataFrame(notes, columns=["at", "kind", "text"]).to_csv(out / "events.csv", index=False)
    pd.DataFrame(broker.log).to_csv(out / "broker_log.csv", index=False)
    s = app.session
    dec = pd.read_sql(
        "select d.id, d.asof, d.status, d.cash_switch, d.index_rebalance, d.budget_per_slot, i.code, sc.rank "
        "from decision d join score sc on sc.decision_id=d.id join instrument i on i.id=sc.instrument_id "
        "where sc.selected=1 order by d.asof, sc.rank",
        s.connection(),
    )
    dec.to_csv(out / "decisions.csv", index=False)
    orders = pd.read_sql("select * from trade_order order by id", s.connection())
    orders.to_csv(out / "orders.csv", index=False)
    json.dump({"elapsed_s": round(time.time() - t0), "days": len(days)}, open(out / "meta.json", "w"))
    print("끝", name, round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    main()

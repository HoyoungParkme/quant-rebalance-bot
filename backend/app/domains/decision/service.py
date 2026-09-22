"""판단 서비스 (QBOT-MS-001 DecisionService.decide_month_end / replay).

평가액과 지수 부분은 매매 도메인이 준다. 아직 없으면 portfolio_fn으로 주입한다.
알림은 운영 도메인이 준다. notifier가 None이면 보내지 않는다.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from app.core.errors import DataNotReady
from app.core.pit import PointInTime
from app.domains.decision import scoring
from app.domains.decision.crud import DecisionCrud
from app.domains.decision.models import Decision, Score, StrategyConfig
from app.domains.marketdata.service import MarketDataService

RESEARCH_TOP60 = Path(__file__).resolve().parents[4] / "docs" / "research" / "05-paper-run-1m" / "top60_by_month.csv"
DEFAULT_FACTORS = {"EP": 1, "SP": 1, "ROE": 1, "OPG": 1, "OPG_Q": 1, "VOL60": -1, "TURN20": -1}
EXCLUDING_STATUSES = {"managed", "warning", "danger", "halted", "liquidation"}


@dataclass(frozen=True)
class Portfolio:
    total: int  # 총 평가액
    index_value: int  # 지수 상장지수펀드 부분 평가액


@dataclass(frozen=True)
class ExecutionPlan:
    """판단 하나를 주문으로 옮기는 데 필요한 것 전부. 매매 도메인이 이것만 보고 실행한다."""

    picks: list[str]  # 점수 순
    index_weight: float
    n_holdings: int
    index_rebalance: bool
    cash_switch: bool


@dataclass
class ReplayResult:
    asof: date
    picked: list[str]
    compared_with: str
    matched: bool | None
    diff: list[str]


def seed_default_config(crud: DecisionCrud, effective_from: str) -> StrategyConfig:
    """첫 전략 설정. 값은 QBOT-PRD-001 7장 결정사항과 검증 rules.json."""
    return crud.add_config(
        StrategyConfig(
            factors_json=json.dumps(DEFAULT_FACTORS),
            min_price=1000,
            min_amount20=500_000_000,
            min_mcap=50_000_000_000,
            n_holdings=10,
            trend_filter=0,
            index_weight=0.2,
            effective_from=effective_from,
        )
    )


def _scoring_config(cfg: StrategyConfig) -> scoring.ScoringConfig:
    return scoring.ScoringConfig(
        factors=json.loads(cfg.factors_json),
        min_price=cfg.min_price,
        min_amount20=cfg.min_amount20,
        min_mcap=cfg.min_mcap,
        n_holdings=cfg.n_holdings,
    )


class DecisionService:
    def __init__(
        self,
        session,
        md: MarketDataService,
        portfolio_fn: Callable[[], Portfolio],
        code_version: str,
        replay_portfolio: Portfolio | None = None,
        notifier: Callable[[str, str], None] | None = None,
    ) -> None:
        self.crud = DecisionCrud(session)
        self.md = md
        self.portfolio_fn = portfolio_fn
        # 재현은 오늘 계좌가 아니라 고정된 자금으로 한다. 안 그러면 같은 날짜가 매번 다른 답을 낸다
        self.replay_portfolio = replay_portfolio or Portfolio(total=1_000_000, index_value=0)
        self.code_version = code_version
        self.notifier = notifier

    def _compute(self, pit: PointInTime, cfg: StrategyConfig, portfolio: Portfolio):
        """decide_month_end 2~10단계. 저장·알림은 하지 않는다."""
        if not self.md.has_bars_on(pit):
            raise DataNotReady(f"{pit.asof} 일봉이 없다. 수집이 끝나지 않았다")
        sc = _scoring_config(cfg)
        bars = self.md.bars(pit, 252)
        fin = self.md.financials(pit)
        statuses = self.md.statuses_on(pit)
        scored = scoring.score(bars, fin, self.md.shares(), sc)
        budget = int(portfolio.total * (1 - cfg.index_weight) / cfg.n_holdings)
        excluded = {c for c, st in statuses.items() if st & EXCLUDING_STATUSES}
        sel = scoring.select(scored, budget, cfg.n_holdings, excluded)
        cash_switch = 0
        if cfg.trend_filter:
            closes = self.md.index_month_ends(pit, "KOSPI", 10)
            # 검증 simulate.py 15행: 있는 만큼(최대 10개)의 평균보다 높을 때만 "상승". 아니면 현금
            if closes and not closes[-1] > sum(closes) / len(closes):
                sel.picked, cash_switch = [], 1
                sel.top60["selected"] = False  # 저장되는 상위 60에도 선정 없음으로
        index_rebalance = 0
        if cfg.index_weight > 0 and portfolio.total > 0:
            cur = portfolio.index_value / portfolio.total
            index_rebalance = int(abs(cur - cfg.index_weight) > 0.05)
        return sel, budget, cash_switch, index_rebalance

    def _store(
        self,
        pit: PointInTime,
        mode: str,
        cfg: StrategyConfig,
        sel,
        budget,
        cash_switch,
        index_rebalance,
        status,
    ):
        ids = self.crud.instrument_ids()
        d = Decision(
            asof=pit.asof.isoformat(),
            mode=mode,
            strategy_config_id=cfg.id,
            code_version=self.code_version,
            cash_switch=cash_switch,
            index_rebalance=index_rebalance,
            status=status,
            budget_per_slot=budget,
        )
        fnames = list(json.loads(cfg.factors_json))
        scores = [
            Score(
                instrument_id=ids[code],
                factors_json=json.dumps({f: _f(row[f]) for f in fnames}),
                pct_json=json.dumps({f: _f(row["pct_" + f]) for f in fnames}),
                total=float(row["total"]),
                rank=int(row["rank"]),
                selected=int(bool(row["selected"])),
                skip_reason=None if pd.isna(row["skip_reason"]) else str(row["skip_reason"]),
            )
            for code, row in sel.top60.iterrows()
        ]
        return self.crud.add_decision(d, scores)

    def decide_month_end(self, asof: date, mode: str) -> Decision:
        """QBOT-MS-001 DecisionService.decide_month_end."""
        existing = self.crud.real_decision(asof.isoformat(), mode)
        if existing is not None:
            return existing
        pit = PointInTime(asof)
        cfg = self.crud.config_effective(asof.isoformat())
        if cfg is None:
            raise DataNotReady("전략 설정이 없다. install로 기본 설정을 만든다")
        sel, budget, cash_switch, index_rebalance = self._compute(pit, cfg, self.portfolio_fn())
        d = self._store(pit, mode, cfg, sel, budget, cash_switch, index_rebalance, "pending")
        if self.notifier:
            text = f"{asof} 판단 저장. 선정 {len(sel.picked)}종목: {', '.join(sel.picked) or '없음'}"
            if cash_switch:
                text += " (하락장 현금 전환)"
            self.notifier("order", text)
        return d

    def _replay_portfolio(self, real, cfg) -> Portfolio:
        """재현의 자금. 그날 실제 판단이 있으면 그때 쓴 예산을 되살리고, 없으면 고정값."""
        if real is not None and real.budget_per_slot:
            return _portfolio_from_budget(real.budget_per_slot, cfg)
        return self.replay_portfolio

    def replay(self, asof: date, compare_to: str = "stored", portfolio: Portfolio | None = None) -> ReplayResult:
        """QBOT-MS-001 DecisionService.replay. 주문·알림 없음. status=replay로 저장."""
        pit = PointInTime(asof)
        cfg = self.crud.config_effective(asof.isoformat())
        if cfg is None:
            raise DataNotReady("전략 설정이 없다")
        real = self.crud.real_decision(asof.isoformat(), "paper") or self.crud.real_decision(asof.isoformat(), "live")
        pf = portfolio or self._replay_portfolio(real, cfg)
        sel, budget, cash_switch, index_rebalance = self._compute(pit, cfg, pf)
        d = self._store(pit, "paper", cfg, sel, budget, cash_switch, index_rebalance, "replay")
        got_rank = list(sel.top60.index)
        if compare_to == "stored":
            if real is None:
                compare_to = "research_file"  # 저장된 판단이 없으면 검증 파일과 비교 (QBOT-API-001 replay)
            else:
                ref = self.crud.picks(real.id)
                return ReplayResult(asof, sel.picked, "stored", ref == sel.picked, _diff(ref, sel.picked))
        if compare_to == "research_file":
            ref = _research_top60(asof)
            if not ref:
                return ReplayResult(asof, sel.picked, "research_file", None, ["검증 파일에 그 날짜가 없다"])
            got = got_rank[: len(ref)]
            return ReplayResult(asof, sel.picked, "research_file", ref == got, _diff(ref, got))
        _ = d
        return ReplayResult(asof, sel.picked, "none", None, [])


def _portfolio_from_budget(budget: int, cfg: StrategyConfig) -> Portfolio:
    return Portfolio(total=round(budget * cfg.n_holdings / max(1 - cfg.index_weight, 1e-9)), index_value=0)


def _f(v) -> float | None:
    return None if pd.isna(v) else float(v)


def _diff(ref: list[str], got: list[str]) -> list[str]:
    out = []
    for k, (a, b) in enumerate(zip(ref, got, strict=False), 1):
        if a != b:
            out.append(f"{k}위: 검증 {a} / 봇 {b}")
    if len(ref) != len(got):
        out.append(f"길이: 검증 {len(ref)} / 봇 {len(got)}")
    return out


def _research_top60(asof: date) -> list[str]:
    if not RESEARCH_TOP60.exists():
        return []
    rows = [r for r in csv.DictReader(open(RESEARCH_TOP60)) if r["asof"] == asof.isoformat()]
    return [r["code"] for r in sorted(rows, key=lambda r: int(r["rank"]))]


def plan_of(crud: DecisionCrud, decision: Decision) -> ExecutionPlan:
    """판단 → 실행 계획. 매매 도메인에 주입한다(매매가 판단 테이블을 직접 읽지 않게)."""
    cfg = crud.config(decision.strategy_config_id)
    if cfg is None:
        raise DataNotReady(f"판단 {decision.id}의 전략 설정이 없다")
    return ExecutionPlan(
        picks=crud.picks(decision.id),
        index_weight=cfg.index_weight,
        n_holdings=cfg.n_holdings,
        index_rebalance=bool(decision.index_rebalance),
        cash_switch=bool(decision.cash_switch),
    )

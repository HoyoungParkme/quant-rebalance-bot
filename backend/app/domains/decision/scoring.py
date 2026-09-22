"""종목 점수와 선정. 순수 함수. DB도 시계도 모른다 (QBOT-DOM-002 Scorer, QBOT-MS-001 Scorer.score/select).

검증 코드(docs/research/03-factor-validation/scripts/build_panel.py, 05-paper-run-1m/scripts/simulate.py)와
같은 계산이다. 여기서 식을 바꾸면 검증 결과가 더는 이 봇을 뒷받침하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.errors import DataNotReady

FACTOR_NAMES = ("EP", "SP", "ROE", "OPG", "OPG_Q", "VOL60", "TURN20")


@dataclass(frozen=True)
class ScoringConfig:
    """strategy_config 행의 순수 값 부분."""

    factors: dict[str, int]  # 이름 → +1(높을수록) / -1(낮을수록)
    min_price: int = 1000
    min_amount20: int = 500_000_000
    min_mcap: int = 50_000_000_000
    n_holdings: int = 10


@dataclass
class Selection:
    picked: list[str]
    skipped: dict[str, str] = field(default_factory=dict)
    top60: pd.DataFrame = field(default_factory=pd.DataFrame)


def compute_factors(bars: dict[str, pd.DataFrame], fin: pd.DataFrame, shares: pd.Series) -> pd.DataFrame:
    """일봉·재무에서 지표 7개와 대상 필터용 값을 계산한다. 인덱스는 종목 코드.

    bars: {"close","open","volume","amount"} 각각 날짜(오름차순) × 종목. 마지막 행이 기준일.
    fin: MarketDataService.financials 출력. shares: 종목별 상장 주식 수.
    """
    if bars["close"].empty:
        raise DataNotReady("일봉이 없어 지표를 계산할 수 없다")
    close = bars["close"].where(bars["close"] > 0).ffill()  # 0원(미거래) 종가는 이전 값으로 (build_panel.py)
    amount = bars["amount"]  # NaN(상장 전·미수집)은 평균의 분모에서 빠진다 (build_panel.py의 mean과 같음)
    last = close.iloc[-1]
    mcap = last * shares.reindex(last.index)
    ret = close.pct_change()
    out = pd.DataFrame(index=last.index.rename("code"))
    out["close"] = last
    out["mcap"] = mcap
    out["tv20"] = amount.iloc[-20:].mean()
    out["traded"] = bars["volume"].iloc[-1].fillna(0) > 0
    out["VOL60"] = ret.iloc[-60:].std()
    out["TURN20"] = amount.iloc[-20:].sum(min_count=1) / mcap
    f = fin.reindex(last.index)
    out["EP"] = f.net_income / mcap
    out["SP"] = f.revenue / mcap
    out["ROE"] = f.net_income / f.equity.where(f.equity > 0)
    prev = f.op_annual_prev.where(f.op_annual_prev > 0)
    out["OPG"] = ((f.operating_income - prev) / prev).clip(-1, 5)
    qp = f.op_q_prev.where(f.op_q_prev.abs() >= 1e8)
    out["OPG_Q"] = ((f.op_q - qp) / qp.abs()).clip(-3, 5)  # 검증 quarterly.py와 같은 절단
    return out


def apply_universe(factors: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    """대상 필터 (QBOT-PRD-001 R4). 지표가 하나라도 NaN인 종목도 뺀다."""
    f = factors
    m = (f.close >= cfg.min_price) & (f.tv20 >= cfg.min_amount20) & (f.mcap >= cfg.min_mcap) & f.traded
    return f[m].dropna(subset=list(cfg.factors))


def score_from_factors(factors: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    """지표 → 백분위 → 합산 점수 → 순위. 검증 simulate.py 16행과 같은 식.

    낮을수록 좋은 지표는 부호 -1을 곱한다. (1 - pct)와 순서가 같다.
    """
    # 지표가 하나라도 없는 종목은 순위 계산 전에 뺀다. 백분위의 분모가 달라지기 때문이다 (simulate.py 16행)
    out = factors.dropna(subset=list(cfg.factors)).rename_axis("code").copy()
    total = pd.Series(0.0, index=out.index)
    for name, sign in cfg.factors.items():
        pct = out[name].rank(pct=True)
        out["pct_" + name] = pct
        total = total + sign * pct
    out["total"] = total
    out = out.sort_values("total", ascending=False, kind="mergesort")
    # 동점은 종목 코드 오름차순 (QBOT-MS-001 Scorer.score 5단계)
    out = (
        out.reset_index()
        .sort_values(["total", "code"], ascending=[False, True], kind="mergesort")
        .set_index("code")
    )
    out["rank"] = np.arange(1, len(out) + 1)
    return out


def score(
    bars: dict[str, pd.DataFrame], fin: pd.DataFrame, shares: pd.Series, cfg: ScoringConfig
) -> pd.DataFrame:
    """QBOT-MS-001 Scorer.score."""
    return score_from_factors(apply_universe(compute_factors(bars, fin, shares), cfg), cfg)


def select(scored: pd.DataFrame, budget_per_slot: int, n: int, excluded: set[str]) -> Selection:
    """QBOT-MS-001 Scorer.select. 순위대로 내려가며 제외·가격 초과 종목을 건너뛴다."""
    picked: list[str] = []
    skipped: dict[str, str] = {}
    for code, row in scored.iterrows():
        if len(picked) >= n:
            break
        if code in excluded:
            skipped[code] = "status"
        elif row.close > budget_per_slot:
            skipped[code] = "price"
        else:
            picked.append(code)
    # 저장 대상: 상위 60 + 순회하며 선정·건너뛴 행 전부. 60위 밖에서 뽑힌 종목이 저장에서 빠지면 안 된다
    keep = scored.index.isin(list(scored.index[:60]) + picked + list(skipped))
    top = scored[keep].copy()
    top["selected"] = top.index.isin(picked)
    top["skip_reason"] = pd.Series(skipped, dtype="object").reindex(top.index)
    return Selection(picked=picked, skipped=skipped, top60=top)

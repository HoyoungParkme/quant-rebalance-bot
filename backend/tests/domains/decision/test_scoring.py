"""Scorer (QBOT-MS-001 Scorer.score/select 테스트 관점).

검증 패널 표본(5개 월말)으로 순위·선정이 검증 스크립트의 식과 100% 일치하는지 본다.
로컬에 20개월 전체 패널이 있으면 그것도 돈다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domains.decision import scoring as sc

FIX = Path(__file__).resolve().parents[2] / "fixtures"
RESEARCH = Path(__file__).resolve().parents[4] / "docs" / "research" / "05-paper-run-1m"
CFG = sc.ScoringConfig(factors=json.load(open(RESEARCH / "rules.json"))["factors"])


def research_rank(g: pd.DataFrame) -> pd.DataFrame:
    """simulate.py 16행 그대로. 단 동점은 종목 코드 순으로 고정한다.

    검증 코드의 동점 순서는 정렬 알고리즘에 따라 임의였다(한 월말에 동점 그룹 75개).
    명세(QBOT-MS-001 Scorer.score 5단계)는 코드 오름차순으로 정했다. 점수 자체는 그대로 비교한다.
    """
    g = g.dropna(subset=list(CFG.factors))
    s = sum(sg * g[f].rank(pct=True) for f, sg in CFG.factors.items())
    return g.assign(score=s).sort_values(["score", "code"], ascending=[False, True], kind="mergesort")


def research_pick(rank: pd.DataFrame, budget: float, n: int) -> list[str]:
    """simulate.py 24~26행 그대로."""
    out = []
    for r in rank.itertuples():
        if len(out) >= n:
            break
        if r.close <= budget:
            out.append(r.code)
    return out


def _check_panel(panel: pd.DataFrame):
    for d, g in panel.groupby("date"):
        ref = research_rank(g)
        got = sc.score_from_factors(g.set_index("code"), CFG)
        # 점수 자체가 같다
        assert np.allclose(got.total.reindex(ref.code).values, ref.score.values)
        # 상위 60 순서: 동점이 없으면 완전히 같아야 한다
        top_ref = list(ref.code.head(60))
        top_got = list(got.index[:60])
        assert top_ref == top_got, d
        for budget in (100_000, 30_000, 300_000):
            assert research_pick(ref, budget, 10) == sc.select(got, budget, 10, set()).picked, (d, budget)


def test_matches_research_on_sample():
    panel = pd.read_csv(FIX / "research_panel_sample.csv", dtype={"code": str})
    assert panel.date.nunique() == 5
    _check_panel(panel)


FULL = os.environ.get("QBOT_RESEARCH_PANEL")


@pytest.mark.skipif(not FULL or not Path(FULL).exists(), reason="로컬 전체 패널 없음")
def test_matches_research_on_all_months():
    panel = pd.read_csv(FULL, dtype={"code": str})
    assert panel.date.nunique() >= 20
    _check_panel(panel)


def test_deterministic_bytes():
    panel = pd.read_csv(FIX / "research_panel_sample.csv", dtype={"code": str})
    g = panel[panel.date == panel.date.iloc[0]].set_index("code")
    a = sc.score_from_factors(g, CFG).to_csv().encode()
    b = sc.score_from_factors(g.copy(), CFG).to_csv().encode()
    assert a == b


def test_select_skips_and_reasons():
    scored = pd.DataFrame(
        {"close": [500_000, 10_000, 20_000, 30_000], "total": [4, 3, 2, 1]},
        index=pd.Index(["A", "B", "C", "D"], name="code"),
    )
    sel = sc.select(scored, 100_000, 2, {"B"})
    assert sel.picked == ["C", "D"]
    assert sel.skipped == {"A": "price", "B": "status"}
    assert list(sel.top60.selected) == [False, False, True, True]


def test_select_fewer_candidates_than_n():
    scored = pd.DataFrame({"close": [1, 2], "total": [2, 1]}, index=pd.Index(["A", "B"], name="code"))
    assert sc.select(scored, 10, 5, set()).picked == ["A", "B"]


def test_compute_factors_formulas():
    """build_panel.py의 식과 같은지 작은 표본으로 직접 계산해 비교."""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2025-01-01", periods=80, freq="B").strftime("%Y-%m-%d")
    close = pd.DataFrame(1000 + rng.normal(0, 10, (80, 2)).cumsum(0), index=dates, columns=["X", "Y"])
    amount = pd.DataFrame(rng.uniform(1e8, 2e9, (80, 2)), index=dates, columns=["X", "Y"])
    volume = pd.DataFrame(np.ones((80, 2)), index=dates, columns=["X", "Y"])
    bars = {"close": close, "open": close, "volume": volume, "amount": amount}
    fin = pd.DataFrame(
        {
            "revenue": [5e11, 1e11],
            "operating_income": [1e10, 2e10],
            "net_income": [8e9, 1e9],
            "equity": [5e10, 2e10],
            "op_annual_prev": [5e9, -1e9],
            "op_q": [3e9, 1e9],
            "op_q_prev": [2e9, 5e7],
            "shares": [1e6, 2e6],
        },
        index=["X", "Y"],
    )
    shares = fin.shares
    f = sc.compute_factors(bars, fin, shares)
    mc = close.iloc[-1] * shares
    assert np.isclose(f.VOL60["X"], close.pct_change().iloc[-60:].std()["X"])
    assert np.isclose(f.TURN20["X"], amount.iloc[-20:].sum()["X"] / mc["X"])
    assert np.isclose(f.EP["X"], 8e9 / mc["X"])
    assert np.isclose(f.OPG["X"], (1e10 - 5e9) / 5e9)
    assert np.isnan(f.OPG["Y"])  # 전년 영업이익이 음수면 성장률 없음
    assert np.isclose(f.OPG_Q["X"], (3e9 - 2e9) / 2e9)
    assert np.isnan(f.OPG_Q["Y"])  # |전년 동기| < 1억이면 없음


def test_zero_close_is_masked_and_short_history_tv20():
    dates = pd.date_range("2025-01-01", periods=70, freq="B").strftime("%Y-%m-%d")
    close = pd.DataFrame({"X": 1000.0, "Y": 2000.0}, index=dates)
    close.iloc[-5, 0] = 0  # 미거래일 0원
    amount = pd.DataFrame({"X": 1e9, "Y": np.nan}, index=dates)
    amount.iloc[-10:, 1] = 2e9  # Y는 상장 10일째
    volume = pd.DataFrame(1.0, index=dates, columns=["X", "Y"])
    bars = {"close": close, "open": close, "volume": volume, "amount": amount}
    fin = pd.DataFrame(
        {
            "revenue": [1, 1],
            "operating_income": [1, 1],
            "net_income": [1, 1],
            "equity": [1, 1],
            "op_annual_prev": [1, 1],
            "op_q": [1, 1],
            "op_q_prev": [1e9, 1e9],
            "shares": [1, 1],
        },
        index=["X", "Y"],
    )
    f = sc.compute_factors(bars, fin, fin.shares)
    assert f.index.name == "code"
    assert f.VOL60["X"] == 0  # 0원이 이전 값으로 채워져 변동성 0
    assert f.tv20["Y"] == 2e9  # 10일치 평균, 20으로 나누지 않는다


def test_empty_bars_raise_data_not_ready():
    from app.core.errors import DataNotReady

    with pytest.raises(DataNotReady):
        sc.compute_factors(
            {
                "close": pd.DataFrame(),
                "open": pd.DataFrame(),
                "volume": pd.DataFrame(),
                "amount": pd.DataFrame(),
            },
            pd.DataFrame(),
            pd.Series(dtype=float),
        )

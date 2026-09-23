"""08 연구: 7지표에 BUFFETT·FSCORE를 더하거나 하나씩 바꾼 16개 후보 (README 0절 사전 고정).

실행: DATA=~/.qbot/research-data <연구용 파이썬> swap.py
07의 panel.pkl과 analyze.py(운용·통계 도구)를 그대로 쓴다. 운용 결과는 CACHE/sim08_*.pkl로 남긴다.
"""

from __future__ import annotations

import pathlib
import pickle
import sys

import numpy as np
import pandas as pd
from scipy import stats

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "07-formula-ensemble/scripts"))
import analyze as AN  # noqa: E402 - 07 자료·운용·통계 도구

OUT = HERE.parent
CACHE = AN.CACHE
CASH = 1_000_000
BASE_W = {"EP": 1, "SP": 1, "ROE": 1, "OPG": 1, "OPG_Q": 1, "VOL60": -1, "TURN20": -1}
PNAMES = list(AN.PERIODS)  # [선택(검증), 표본외]


def candidates() -> dict[str, dict[str, int]]:
    out = {"BASE": dict(BASE_W)}
    for extra in ("BUFFETT", "FSCORE"):
        out[f"ADD_{extra}"] = {**BASE_W, extra: 1}
    for extra in ("BUFFETT", "FSCORE"):
        for x in BASE_W:
            w = {k: v for k, v in BASE_W.items() if k != x}
            w[extra] = 1
            out[f"{extra}_for_{x}"] = w
    return out


CANDS = candidates()
assert len(CANDS) == 17  # BASE + 16


def score_month(g: pd.DataFrame, w: dict[str, int]) -> pd.Series:
    extra = [k for k in w if k not in BASE_W]
    if any(g[k].notna().sum() == 0 for k in extra):
        w = BASE_W  # 사전 고정: 추가 지표가 아무 종목에도 없는 달은 BASE와 같다
    full = g.dropna(subset=list(w))
    return sum(sg * full[k].rank(pct=True) for k, sg in w.items()).reindex(g.index)


def ranks(w: dict[str, int]) -> tuple[dict, pd.Series]:
    out, ics = {}, {}
    for t, g in AN.FR.items():
        s = score_month(g, w)
        out[t] = AN.order(g, s)
        x = pd.DataFrame({"s": s, "f": g.fwd}).dropna()
        if len(x) >= 50:
            ics[t] = stats.spearmanr(x.s, x.f).statistic
    return out, pd.Series(ics)


if __name__ == "__main__":
    # BASE 재현 확인: 7지표 합성 = panel의 BASE
    diffs = [
        (score_month(g, BASE_W) - g.BASE).abs().max() for g in AN.FR.values() if g.BASE.notna().any()
    ]
    print("BASE 재현 최대 차이:", float(np.nanmax(diffs)), flush=True)
    assert np.nanmax(diffs) < 1e-9

    rows, mret, ic_all = [], {}, {}
    for name, w in CANDS.items():
        rk, ic = ranks(w)
        ic_all[name] = ic
        cache = CACHE / ("sim_BASE.pkl" if name == "BASE" else f"sim08_{name}.pkl")
        if cache.exists():
            res = pickle.load(open(cache, "rb"))
        else:
            res = {}
            for pname, (s, e) in AN.PERIODS.items():
                E, cost, trades = AN.simulate(rk, s, e, CASH, top60=True, cap=None)
                res[pname] = (E, cost, trades, None)
            pickle.dump(res, open(cache, "wb"))
        for pname, per in zip(PNAMES, (AN.SEL, AN.OOS)):
            E, cost, trades, _ = res[pname]
            icp = ic[AN.in_period(ic.index, per)]
            t_ = AN.nw_t(icp)
            yrs = (E.index[-1] - E.index[0]).days / 365.25
            rows.append(
                {
                    "후보": name,
                    "구간": pname,
                    "평균 IC": round(icp.mean(), 4),
                    "IC t(NW3)": round(t_, 2),
                    "p": 2 * (1 - stats.norm.cdf(abs(t_))),
                    **AN.eng.metrics(E.equity, CASH),
                    "연 매매 건수": round(trades / yrs, 1),
                }
            )
            mret[(name, pname)] = AN.monthly(E.equity, CASH)
        print(name, "끝", flush=True)

    R = pd.DataFrame(rows)
    for pname in PNAMES:
        m = R["구간"] == pname
        R.loc[m, "홀름 p"] = AN.holm(R.loc[m].set_index("후보").p).values
        M = pd.DataFrame({n: mret[(n, pname)] for n in CANDS})
        srs = (M.mean() / M.std()).tolist()
        for n in CANDS:
            R.loc[m & (R["후보"] == n), "DSR(N=17)"] = round(AN.dsr(M[n], srs, 17), 3)
    pbo = AN.pbo_cscv(pd.DataFrame({n: mret[(n, PNAMES[0])] for n in CANDS}).dropna())

    # 사전 고정 선택 규칙 (선택 구간만)
    S = R[R["구간"] == PNAMES[0]].set_index("후보")
    b = S.loc["BASE"]
    ok = S[(S.index != "BASE") & (S["연복리%"] > b["연복리%"]) & (S["최대 손실폭%"] >= b["최대 손실폭%"] - 2)]
    pick = ok["평균 IC"].idxmax() if len(ok) else None

    R["선택에 씀"] = np.where(R["구간"] == PNAMES[0], "예", "아니오(표본외, 선택에 쓰지 않음)")
    R.to_csv(OUT / "results.csv", index=False)
    pd.DataFrame(ic_all).to_csv(OUT / "ic_monthly.csv")
    summary = {"PBO(선택 구간, 17열, CSCV 8블록)": pbo, "조건 통과 후보 수": len(ok), "선택": pick or "채택 없음"}
    if pick:
        O_ = R[(R["구간"] == PNAMES[1])].set_index("후보")
        p, bo = O_.loc[pick], O_.loc["BASE"]
        verdict = (p["연복리%"] > bo["연복리%"]) and (p["최대 손실폭%"] >= bo["최대 손실폭%"] - 2) and pbo < 0.5
        summary["판정"] = "조건부 후보(모의 운용 병행 관찰 권고)" if verdict else "기각"
    else:
        summary["판정"] = "채택 없음"
    pd.Series(summary).to_csv(OUT / "summary.csv", header=["값"])
    pd.set_option("display.width", 250)
    print(R.drop(columns=["p"]).to_string())
    print(summary)

"""07 연구 테마·업종 분석.

(a) 수익률 상관으로 묶은 "데이터 테마"와 업종의 일치도, 업종 동조화 순열검정
(b) 재무 특성·수익률 주성분으로 업종을 판별하는 LDA (5겹 교차검증)
(d) 테마·업종 평균 수익률의 모멘텀 신호 IC
실행: DATA=~/.qbot/research-data <연구용 파이썬> themes.py
"""

from __future__ import annotations

import pathlib
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.metrics import adjusted_rand_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

warnings.filterwarnings("ignore")

import panel as PN  # noqa: E402

eng = PN.eng
OUT = pathlib.Path(__file__).resolve().parent.parent
C, cal = eng.C, eng.cal
RET = C.pct_change()
P = pd.read_pickle(PN.CACHE / "panel.pkl")
P["code"] = P.code.astype(str)
K_THEMES = 30
FEATS = ["GPA", "GM", "OM", "ATURN", "DE", "CURR", "CASHA", "CAPEXA", "VOL60", "TURN20", "AG", "ROE_A"]
SEL = ("2020-01-31", "2024-11-30")
OOS = ("2024-12-01", "2026-07-31")


def residual_corr(t: pd.Timestamp, codes) -> pd.DataFrame:
    i = cal.get_loc(t)
    r = RET.iloc[i - 119 : i + 1][list(codes)]
    r = r.loc[:, r.notna().sum() >= 100]
    r = r.sub(r.mean(axis=1), axis=0).fillna(0.0)
    return r.corr()


def themes_at(t, codes) -> tuple[pd.Series, pd.DataFrame]:
    cm = residual_corr(t, codes)
    d = (1 - cm.values).clip(0, 2)
    np.fill_diagonal(d, 0)
    Z = linkage(squareform(d, checks=False), "average")
    lab = fcluster(Z, t=K_THEMES, criterion="maxclust")
    return pd.Series(lab, index=cm.index), cm


def within_minus_between(cm: np.ndarray, lab: np.ndarray) -> float:
    M = (lab[:, None] == np.unique(lab)[None, :]).astype(float)
    S = M.T @ cm @ M
    within = np.trace(S) - np.trace(cm)  # 대각(자기 자신) 제외
    n_w = (M.sum(0) ** 2).sum() - len(lab)
    total = cm.sum() - np.trace(cm)
    n_all = len(lab) ** 2 - len(lab)
    return within / n_w - (total - within) / (n_all - n_w)


def nw_t(x, lags=3):
    x = pd.Series(x).dropna().values
    n = len(x)
    e = x - x.mean()
    s = (e @ e) / n
    for k in range(1, lags + 1):
        s += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(s / n))


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    dec = sorted(t for t in P.date.unique() if pd.Timestamp(t).month == 12 and pd.Timestamp(t).year >= 2020)

    # (a) 데이터 테마 vs 업종, 동조화 순열검정 / (b) 업종 판별 LDA
    a_rows, b_rows = [], []
    for t in dec:
        t = pd.Timestamp(t)
        g = P[(P.date == t) & ~P.fin].set_index("code")
        lab, cm = themes_at(t, g.index)
        ind = g.ind.reindex(lab.index).astype(object).fillna("?")  # 판다스 문자열형은 scikit-learn 색인이 안 된다
        big = ind.map(ind.value_counts()) >= 5
        ari = adjusted_rand_score(ind[big], lab[big])
        cmv = cm.values
        ind_codes = pd.factorize(ind)[0]
        obs = within_minus_between(cmv, ind_codes)
        null = [within_minus_between(cmv, rng.permutation(ind_codes)) for _ in range(300)]
        themes_obs = within_minus_between(cmv, lab.values)
        a_rows.append(
            {
                "연말": t.date(),
                "종목 수": len(lab),
                "데이터 테마 vs 업종 ARI": round(ari, 3),
                "업종 안 상관 − 업종 간 상관": round(obs, 4),
                "순열 p(300회)": (np.sum(np.array(null) >= obs) + 1) / 301,
                "데이터 테마 안 − 간": round(themes_obs, 4),
            }
        )
        # (b)
        x = g.reindex(lab.index)
        X = pd.DataFrame({f: x[f].rank(pct=True) for f in FEATS}).fillna(0.5)
        y = ind
        keep = y.map(y.value_counts()) >= 30
        keep &= y != "?"
        Xk, yk = X[keep], y[keep].astype(str)
        cv = StratifiedKFold(5, shuffle=True, random_state=0)
        lda = LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")
        acc = cross_val_score(lda, Xk.values, yk.to_numpy(), cv=cv).mean()
        acc_shuf = cross_val_score(lda, Xk.values, rng.permutation(yk.to_numpy()), cv=cv).mean()
        # 수익률 주성분 10개로 판별
        ev, evec = np.linalg.eigh(cm.values)
        pcs = pd.DataFrame(evec[:, -10:], index=cm.index)
        acc_ret = cross_val_score(lda, pcs[keep].values, yk.to_numpy(), cv=cv).mean()
        b_rows.append(
            {
                "연말": t.date(),
                "업종 수": yk.nunique(),
                "종목 수": len(yk),
                "최빈값 기준": round(yk.value_counts(normalize=True).iloc[0], 3),
                "라벨 섞기 기준": round(acc_shuf, 3),
                "재무 특성 LDA": round(acc, 3),
                "수익률 주성분 LDA": round(acc_ret, 3),
            }
        )
        print(a_rows[-1], b_rows[-1], flush=True)
    pd.DataFrame(a_rows).to_csv(OUT / "theme_vs_industry.csv", index=False)
    pd.DataFrame(b_rows).to_csv(OUT / "industry_lda.csv", index=False)

    # (d) 테마·업종 모멘텀 IC
    mends = sorted(pd.Timestamp(t) for t in P.date.unique())
    rows = []
    for k, t in enumerate(mends):
        if k < 6:
            continue
        g = P[P.date == t].set_index("code")
        g = g[g.fwd.notna()]
        if g.empty:
            continue
        lab, _ = themes_at(t, g.index)
        t1, t6 = mends[k - 1], mends[k - 6]
        r1 = eng.LASTP.loc[t].reindex(g.index) / eng.LASTP.loc[t1].reindex(g.index) - 1
        r6 = eng.LASTP.loc[t].reindex(g.index) / eng.LASTP.loc[t6].reindex(g.index) - 1
        df = pd.DataFrame({"fwd": g.fwd, "r1": r1, "r6": r6, "ind": g.ind, "th": lab.reindex(g.index)})
        out = {"date": t}
        for grp in ["ind", "th"]:
            for w in ["r1", "r6"]:
                sig = df.groupby(grp)[w].transform("mean")
                m = sig.notna() & df.fwd.notna() & df[grp].notna()
                out[f"{grp}_{w}"] = stats.spearmanr(sig[m], df.fwd[m]).statistic if m.sum() > 50 else np.nan
        # 비교: 종목 자신의 과거 수익률(개별 모멘텀)
        for w in ["r1", "r6"]:
            m = df[w].notna()
            out[f"own_{w}"] = stats.spearmanr(df[w][m], df.fwd[m]).statistic
        rows.append(out)
    M = pd.DataFrame(rows).set_index("date")
    res = []
    names = {
        "ind_r1": "업종 1개월",
        "ind_r6": "업종 6개월",
        "th_r1": "데이터 테마 1개월",
        "th_r6": "데이터 테마 6개월",
        "own_r1": "종목 자신 1개월",
        "own_r6": "종목 자신 6개월",
    }
    for col, nm in names.items():
        for pn, per in [("선택", SEL), ("표본외", OOS)]:
            x = M[col][(M.index >= per[0]) & (M.index <= per[1])]
            res.append({"신호": nm, "구간": pn, "개월": x.notna().sum(), "평균 IC": round(x.mean(), 4), "t(NW3)": round(nw_t(x), 2)})
    R = pd.DataFrame(res)
    R.to_csv(OUT / "theme_momentum_ic.csv", index=False)
    print(R.to_string())

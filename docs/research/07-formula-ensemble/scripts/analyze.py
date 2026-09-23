"""07 연구 본 분석: 단일 공식 IC, 10종목 운용, 결합(투표·순위평균·스태킹), 중복, 과최적화 점검.

실행: DATA=~/.qbot/research-data <연구용 파이썬> analyze.py
먼저 panel.py가 CACHE/panel.pkl을 만들어 둬야 한다. 운용 결과는 CACHE/sim_*.pkl로 남겨 끊겨도 이어간다.
"""

from __future__ import annotations

import itertools
import math
import os
import pathlib
import pickle
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score

warnings.filterwarnings("ignore")

import panel as PN  # noqa: E402 - 엔진과 자료를 함께 불러온다

eng = PN.eng
OUT = pathlib.Path(__file__).resolve().parent.parent
CACHE = PN.CACHE
C, O, V, cal, LASTP = eng.C, eng.O, eng.V, eng.cal, eng.LASTP
N = 10
SEL = ("2020-01-31", "2024-11-30")  # 선택 구간의 판단일(수익은 2020-02 ~ 2024-12)
OOS = ("2024-12-01", "2026-07-31")  # 표본외 판단일(수익은 2025-01 ~ 2026-08)
PERIODS = eng.PERIODS  # 운용 구간(06과 같음)

P = pd.read_pickle(CACHE / "panel.pkl")
P["code"] = P.code.astype(str)

SINGLES = {  # 이름: (열, 방향)
    "BASE": ("BASE", 1),
    "GPA": ("GPA", 1),
    "FSCORE": ("FSCORE", 1),
    "NSI": ("NSI", -1),
    "ACC": ("ACC", -1),
    "MAGIC": ("MAGIC", 1),
    "BUFFETT": ("BUFFETT", 1),
}
REFERENCE = {"AG(낮을수록)": ("AG", -1), "ZPP(높을수록)": ("ZPP", 1)}  # 걸러내기용. IC는 참고로만
VOTERS = ["BASE", "GPA", "FSCORE", "MAGIC", "BUFFETT", "NSI", "ACC"]


def pct(s: pd.Series, sign: int = 1) -> pd.Series:
    return (sign * s).rank(pct=True)


# ---------------- 월별 점수 표 ----------------
def month_frames() -> dict[pd.Timestamp, pd.DataFrame]:
    out = {}
    for t, g in P.groupby("date"):
        g = g.set_index("code")
        nf = g[~g.fin].copy()
        nf["MAGIC"] = pct(nf.EY) + pct(nf.GPA)
        nf.loc[nf.EY.isna() | nf.GPA.isna(), "MAGIC"] = np.nan
        g["MAGIC"] = nf.MAGIC.reindex(g.index)
        out[t] = g
    return out


FR = month_frames()


def filtered(g: pd.DataFrame) -> pd.DataFrame:
    """결합 방식의 공통 걸러내기: 금융 제외 → Z″ 하위 10% → 자산성장 상위 20%·하위 10%."""
    x = g[~g.fin]
    z = x.ZPP.rank(pct=True)
    x = x[~(z <= 0.10)]
    a = x.AG.rank(pct=True)
    x = x[~((a > 0.80) | (a <= 0.10))]
    return x


def voter_pcts(x: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({v: pct(x[SINGLES[v][0]], SINGLES[v][1]) for v in VOTERS})


def order(x: pd.DataFrame, score: pd.Series) -> pd.DataFrame:
    """점수 내림차순, 같으면 종목코드 순."""
    r = pd.DataFrame({"close": x.close, "score": score, "ind": x.ind}).dropna(subset=["score"])
    r = r.reset_index(names="code").sort_values(["score", "code"], ascending=[False, True]).set_index("code")
    return r


# ---------------- 전략별 순위표 ----------------
def ranks_single(name: str) -> dict:
    col, sg = SINGLES[name]
    out = {}
    for t, g in FR.items():
        x = g if name == "BASE" else g[~g.fin]
        out[t] = order(x, sg * x[col])
    return out


def ranks_vote() -> dict:
    out = {}
    for t, g in FR.items():
        x = filtered(g)
        pc = voter_pcts(x)
        votes = (pc >= 0.8).sum(axis=1)
        tie = pc.fillna(0.5).mean(axis=1)
        out[t] = order(x, votes + tie / 10)  # 표가 먼저, 평균 백분위는 동점 가르기(0~0.1)
    return out


def ranks_avg() -> dict:
    return {t: order(x, voter_pcts(x).fillna(0.5).mean(axis=1)) for t, x in ((t, filtered(g)) for t, g in FR.items())}


def make_model(kind: str):
    if kind == "LOGIT":
        return LogisticRegression(max_iter=1000)
    if kind == "LDA":
        return LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")
    return HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.05, random_state=0)


def training_rows():
    """월마다 (특성, 라벨). 라벨: 다음 달 수익률 상위 20%=1, 하위 20%=0, 가운데는 뺀다."""
    feats = {}
    for t, g in FR.items():
        x = filtered(g)
        pc = voter_pcts(x).fillna(0.5)
        q = x.fwd.rank(pct=True)
        y = pd.Series(np.where(q > 0.8, 1, np.where(q <= 0.2, 0, -1)), index=x.index).where(x.fwd.notna(), -1)
        feats[t] = (pc, y)
    return feats


FEATS = training_rows()


def ranks_stack(kind: str, clf_log: list) -> dict:
    out, model, fitted_year = {}, None, None
    for t in sorted(FR):
        pc, y = FEATS[t]
        x = filtered(FR[t])
        if t.year >= 2021 and fitted_year != t.year:
            tr = [FEATS[s] for s in FEATS if s.year <= t.year - 1]  # 그 전 해 12월 판단까지(결과는 1월 말에 확정)
            Xtr = pd.concat([a for a, b in tr])
            ytr = pd.concat([b for a, b in tr])
            m = ytr >= 0
            model = make_model(kind).fit(Xtr[m].values, ytr[m].values)
            fitted_year = t.year
        if model is None:
            score = pc.mean(axis=1)  # 2020년은 순위 평균
        else:
            score = pd.Series(model.predict_proba(pc.values)[:, 1], index=pc.index)
            m = y >= 0
            if m.sum() > 20 and y[m].nunique() == 2:
                clf_log.append(
                    {
                        "model": kind,
                        "date": t,
                        "acc": accuracy_score(y[m], (score[m] > 0.5).astype(int)),
                        "auc": roc_auc_score(y[m], score[m]),
                    }
                )
        out[t] = order(x, score)
    return out


# ---------------- 운용 (06 simulate와 같은 체결 규칙, 선택만 바꿀 수 있게) ----------------
def simulate(scores: dict, start: str, end: str, cash0: float, top60: bool, cap: int | None):
    days = cal[(cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))]
    mends = set(np.datetime64(t) for t in scores)
    cash, hold, cost, trades = float(cash0), {}, 0.0, 0
    eq = []
    seed = max(t for t in scores if t < days[0])
    pending = scores[seed]
    tax = eng.tax

    def sell(d, c):
        nonlocal cash, cost, trades
        n, bp, bd = hold.pop(c)
        o = O.at[d, c] if pd.notna(C.at[d, c]) else LASTP.at[d, c]
        px = o * (1 - eng.SLIP)
        gross = n * px
        fee = gross * (eng.FEE + tax(d))
        cash += gross - fee
        cost += fee + n * o * eng.SLIP
        trades += 1

    def tradable(d, c):
        return pd.notna(C.at[d, c]) and V.at[d, c] > 0 and O.at[d, c] > 0

    def buy(d, c, eq_open):
        nonlocal cash, cost, trades
        if c in hold or len(hold) >= N or not tradable(d, c):
            return
        px = O.at[d, c] * (1 + eng.SLIP)
        n = int(min(eq_open / N, cash) // (px * (1 + eng.FEE)))
        if n >= 1:
            cash -= n * px * (1 + eng.FEE)
            cost += n * px * eng.FEE + n * O.at[d, c] * eng.SLIP
            hold[c] = (n, px * (1 + eng.FEE), d)
            trades += 1

    def target(rank, eq_open):
        r = rank.head(60) if top60 else rank
        r = r[r.close <= eq_open / N]
        if cap is None:
            return r.index[:N].tolist()
        out, cnt = [], {}
        for c, ind in zip(r.index, r.ind.fillna("?")):
            if cnt.get(ind, 0) >= cap:
                continue
            out.append(c)
            cnt[ind] = cnt.get(ind, 0) + 1
            if len(out) >= N:
                break
        return out

    for d in days:
        eq_open = cash + sum(
            n * (O.at[d, c] if (pd.notna(C.at[d, c]) and O.at[d, c] > 0) else LASTP.at[d, c])
            for c, (n, _, _) in hold.items()
        )
        for c in [c for c in hold if pd.isna(C.at[d, c])]:
            sell(d, c)
        if pending is not None:
            tg = target(pending, eq_open)
            for c in list(hold):
                if c not in tg and tradable(d, c):
                    sell(d, c)
            for c in tg:
                buy(d, c, eq_open)
            pending = None
        if np.datetime64(d) in mends and d in scores:
            pending = scores[d]
        val = cash + sum(n * (C.at[d, c] if pd.notna(C.at[d, c]) else LASTP.at[d, c]) for c, (n, _, _) in hold.items())
        eq.append((d, val, len(hold)))
    E = pd.DataFrame(eq, columns=["date", "equity", "n"]).set_index("date")
    return E, cost, trades


# ---------------- 통계 도구 ----------------
def nw_t(x: pd.Series, lags: int = 3) -> float:
    x = x.dropna().values
    n = len(x)
    if n < 3:
        return float("nan")
    e = x - x.mean()
    s = (e @ e) / n
    for k in range(1, lags + 1):
        w = 1 - k / (lags + 1)
        s += 2 * w * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def holm(p: pd.Series) -> pd.Series:
    order_ = p.sort_values()
    m = len(order_)
    adj, run = {}, 0.0
    for i, (k, v) in enumerate(order_.items()):
        run = max(run, min(1.0, (m - i) * v))
        adj[k] = run
    return pd.Series(adj).reindex(p.index)


def ic_series(col: str, sg: int, use_fin: bool) -> pd.DataFrame:
    rows = []
    for t, g in FR.items():
        x = g if use_fin else g[~g.fin]
        x = x[[col, "fwd"]].dropna()
        if len(x) < 50:
            continue
        ic = stats.spearmanr(sg * x[col], x.fwd).statistic
        q = (sg * x[col]).rank(pct=True)
        spread = x.fwd[q > 0.9].mean() - x.fwd[q <= 0.1].mean()
        rows.append((t, ic, spread))
    return pd.DataFrame(rows, columns=["date", "ic", "spread"]).set_index("date")


def in_period(idx, per):
    return (idx >= pd.Timestamp(per[0])) & (idx <= pd.Timestamp(per[1]))


def monthly(E: pd.Series, cash0: float) -> pd.Series:
    m = E.resample("ME").last()
    r = m.pct_change()
    r.iloc[0] = m.iloc[0] / cash0 - 1
    return r


def dsr(r: pd.Series, sr_all: list[float], n_trials: int) -> float:
    r = r.dropna()
    T = len(r)
    sr = r.mean() / r.std(ddof=1)
    g3, g4 = stats.skew(r), stats.kurtosis(r, fisher=False)
    var_sr = np.var(sr_all, ddof=1) if len(sr_all) > 1 else 0.0
    em = 0.5772156649
    sr0 = math.sqrt(var_sr) * ((1 - em) * stats.norm.ppf(1 - 1 / n_trials) + em * stats.norm.ppf(1 - 1 / (n_trials * math.e)))
    den = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr**2))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))


def pbo_cscv(M: pd.DataFrame, S: int = 8) -> float:
    """M: 월 × 전략 수익. 조합마다 표본내 최고 전략의 표본외 상대순위로 과최적화 확률."""
    blocks = np.array_split(np.arange(len(M)), S)
    lam = []
    for comb in itertools.combinations(range(S), S // 2):
        is_idx = np.concatenate([blocks[i] for i in comb])
        os_idx = np.concatenate([blocks[i] for i in range(S) if i not in comb])
        sr_is = M.iloc[is_idx].mean() / M.iloc[is_idx].std()
        sr_os = M.iloc[os_idx].mean() / M.iloc[os_idx].std()
        best = sr_is.idxmax()
        w = sr_os.rank(pct=True)[best] * len(M.columns) / (len(M.columns) + 1)
        lam.append(math.log(w / (1 - w)))
    return float(np.mean(np.array(lam) <= 0))


def block_boot_ci(d: pd.Series, B: int = 5000, L: int = 3, seed: int = 0) -> tuple[float, float]:
    x = d.dropna().values
    n = len(x)
    rng = np.random.default_rng(seed)
    k = math.ceil(n / L)
    means = np.empty(B)
    for b in range(B):
        st = rng.integers(0, n - L + 1, size=k)
        s = np.concatenate([x[i : i + L] for i in st])[:n]
        means[b] = s.mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ---------------- 실행 ----------------
if __name__ == "__main__":
    CASH = 1_000_000
    (OUT / "equity").mkdir(exist_ok=True)

    # 1. IC
    ic_rows, ic_ts = [], {}
    for name, (col, sg) in {**SINGLES, **REFERENCE}.items():
        s = ic_series(col, sg, use_fin=(name == "BASE"))
        ic_ts[name] = s.ic
        for pname, per in [("선택 2020-01~2024-11 판단", SEL), ("표본외 2024-12~2026-07 판단", OOS)]:
            x = s[in_period(s.index, per)]
            t_ = nw_t(x.ic)
            ic_rows.append(
                {
                    "공식": name,
                    "구간": pname,
                    "개월": len(x),
                    "평균 IC": round(x.ic.mean(), 4),
                    "IC t(NW3)": round(t_, 2),
                    "p": 2 * (1 - stats.norm.cdf(abs(t_))),
                    "IC>0 달%": round((x.ic > 0).mean() * 100, 0),
                    "10분위 스프레드(월%)": round(x.spread.mean() * 100, 2),
                }
            )
    IC = pd.DataFrame(ic_rows)
    for pname in IC["구간"].unique():
        m = (IC["구간"] == pname) & IC["공식"].isin(list(SINGLES))
        IC.loc[m, "홀름 p"] = holm(IC.loc[m].set_index("공식").p).values
    IC["t>3"] = IC["IC t(NW3)"].abs() > 3
    IC.to_csv(OUT / "ic.csv", index=False)
    print(IC.to_string(), flush=True)

    # 2. 중복
    names = list(SINGLES)
    corr_acc = []
    for t, g in FR.items():
        x = g[~g.fin]
        sc = pd.DataFrame({n: SINGLES[n][1] * x[SINGLES[n][0]] for n in names})
        corr_acc.append(sc.corr(method="spearman"))
    CM = pd.DataFrame(np.nanmean(np.stack([c.values for c in corr_acc]), axis=0), index=names, columns=names)  # 값이 없는 달(BUFFETT 초기)은 빼고 평균
    CM.round(3).to_csv(OUT / "score_corr.csv")
    ev = np.sort(np.linalg.eigvalsh(CM.fillna(0).values))[::-1]
    neff = ev.sum() ** 2 / (ev**2).sum()
    Z = linkage(1 - CM.fillna(0).values[np.triu_indices(len(names), 1)], "average")
    cl = fcluster(Z, t=0.5, criterion="distance")  # 상관 0.5 이상끼리 한 묶음
    pd.DataFrame(
        {"고유값": ev.round(3), "설명력%": (ev / ev.sum() * 100).round(1)}, index=[f"PC{i+1}" for i in range(len(ev))]
    ).to_csv(OUT / "pca.csv")
    pd.Series(cl, index=names, name="묶음(ρ≥0.5)").to_csv(OUT / "score_clusters.csv")
    pd.DataFrame(ic_ts)[names].corr().round(3).to_csv(OUT / "ic_corr.csv")
    print("유효 공식 수", round(neff, 2), dict(zip(names, cl)), flush=True)

    # 3. 전략 순위표
    clf_log: list = []
    RANKS = {n: ranks_single(n) for n in SINGLES}
    RANKS["VOTE"] = ranks_vote()
    RANKS["RANKAVG"] = ranks_avg()
    for k in ["LOGIT", "LDA", "GBM"]:
        RANKS[f"STACK_{k}"] = ranks_stack(k, clf_log)
    CL = pd.DataFrame(clf_log)
    CL["구간"] = np.where(CL.date <= pd.Timestamp(SEL[1]), "선택(2021~2024 판단)", "표본외")
    CL.groupby(["model", "구간"])[["acc", "auc"]].mean().round(3).to_csv(OUT / "stack_classification.csv")
    print(CL.groupby(["model", "구간"])[["acc", "auc"]].mean().round(3), flush=True)

    # 4. 운용
    sim_rows, mret = [], {}
    for name, sc in RANKS.items():
        cache = CACHE / f"sim_{name}.pkl"
        if cache.exists():
            res = pickle.load(open(cache, "rb"))
        else:
            res = {}
            for pname, (s, e) in PERIODS.items():
                kw = {"top60": name == "BASE", "cap": 3 if name == "VOTE" else None}
                E, cost, trades = simulate(sc, s, e, CASH, **kw)
                Es, _, _ = simulate(sc, s, e, CASH * 0.8, **kw)
                blend = Es.equity + eng.etf_series(E.index, CASH * 0.2)
                res[pname] = (E, cost, trades, blend)
            pickle.dump(res, open(cache, "wb"))
        for pname, (E, cost, trades, blend) in res.items():
            yrs = (E.index[-1] - E.index[0]).days / 365.25
            row = {"전략": name, "기간": pname, **eng.metrics(E.equity, CASH)}
            row["연 매매 건수"] = round(trades / yrs, 1)
            row["연 비용률%"] = round(cost / E.equity.mean() / yrs * 100, 2)
            bm = eng.metrics(blend, CASH)
            row["80:20 연복리%"], row["80:20 최대 손실폭%"] = bm["연복리%"], bm["최대 손실폭%"]
            sim_rows.append(row)
            mret[(name, pname)] = monthly(E.equity, CASH)
            E.to_csv(OUT / "equity" / f"{name}_{pname[:2]}.csv")
        print(name, "운용 끝", flush=True)
    SIM = pd.DataFrame(sim_rows)

    # 5. 과최적화 점검
    over = []
    pnames = list(PERIODS)
    for pname in pnames:
        R = pd.DataFrame({n: mret[(n, pname)] for n in RANKS})
        srs = (R.mean() / R.std()).tolist()
        for n in RANKS:
            lo, hi = block_boot_ci(R[n] - R["BASE"]) if n != "BASE" else (np.nan, np.nan)
            over.append(
                {
                    "전략": n,
                    "기간": pname,
                    "월 샤프": round(R[n].mean() / R[n].std(), 3),
                    "DSR(N=12)": round(dsr(R[n], srs, 12), 3),
                    "기준 대비 월 초과 평균%": round((R[n] - R["BASE"]).mean() * 100, 2),
                    "95% 하한%": round(lo * 100, 2),
                    "95% 상한%": round(hi * 100, 2),
                }
            )
    OV = pd.DataFrame(over)
    Rsel = pd.DataFrame({n: mret[(n, pnames[0])] for n in RANKS}).dropna()
    pbo = pbo_cscv(Rsel)
    SIM = SIM.merge(OV, on=["전략", "기간"])
    SIM.to_csv(OUT / "results.csv", index=False)
    print(SIM.to_string(), flush=True)
    print("PBO(선택 구간, 12개 전략, CSCV 8블록):", round(pbo, 3))
    pd.Series({"PBO": pbo, "유효 공식 수": neff}).to_csv(OUT / "overfit_summary.csv")

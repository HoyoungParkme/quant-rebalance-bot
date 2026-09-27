"""11 전수 테스트 집계: 지표 표, 그래프, 엔진 A(봇 실코드)와 B(연구 엔진)의 교차 검증.

실행: DATA=~/.qbot/research-data <연구 파이썬> report.py
입력: ../equity/*.csv(엔진 B), ~/.qbot/research-data/fulltest/a_*/(엔진 A)
출력: ../metrics.csv, ../metrics_yearly.csv, ../crosscheck_*.csv, ../*.png
"""

from __future__ import annotations

import json
import os
import pathlib
import pickle
import sqlite3

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent
DATA = pathlib.Path(os.environ["DATA"]).expanduser()
FT = DATA / "fulltest"
CASH = 10_000_000

for f in font_manager.findSystemFonts():
    if "NotoSansCJK" in f:
        font_manager.fontManager.addfont(f)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False


# ---------------- 기준 ----------------
def benchmarks(days: pd.DatetimeIndex) -> dict[str, pd.Series]:
    ks = pickle.load(open(DATA / "bt/macro.pkl", "rb"))["KOSPI"].reindex(days).ffill()
    c = sqlite3.connect(f"file:{FT / 'base.sqlite3'}?mode=ro", uri=True)
    etf = pd.read_sql(
        "select trade_date, close from daily_bar b join instrument i on i.id=b.instrument_id "
        "where i.code='069500' and series_no=1",
        c,
        parse_dates=["trade_date"],
    ).set_index("trade_date").close
    c.close()
    _, C, _ = pickle.load(open(DATA / "sim/ocv.pkl", "rb"))
    r = C.pct_change(fill_method=None).reindex(days)
    r = r.where(r.abs() < 0.35)  # 가격제한폭(±30%) 밖은 자료 오류로 본다
    ew = (1 + r.mean(axis=1).fillna(0)).cumprod()
    return {
        "코스피(지수)": ks / ks.iloc[0] * CASH,
        "KODEX200 보유": etf.reindex(days).ffill() / etf.reindex(days).ffill().iloc[0] * CASH,
        "전 종목 동일비중": ew / ew.iloc[0] * CASH,
    }


# ---------------- 지표 ----------------
def metrics(E: pd.Series, K: pd.Series, cost: pd.Series | None = None, traded: pd.Series | None = None) -> dict:
    """E·K: 같은 날짜의 평가액(구간 첫날 전날 값을 기준으로 잘라 넣는다)."""
    r = E.pct_change().dropna()
    rk = K.pct_change().reindex(r.index).fillna(0)
    n = len(r)
    yrs = n / 252
    cum = E.iloc[-1] / E.iloc[0] - 1
    cagr = (1 + cum) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = r.std() * np.sqrt(252)
    dd = E / E.cummax() - 1
    # 최대 손실 기간: 고점에서 회복(또는 끝)까지 가장 긴 거래일 수
    peak_i, longest, cur_start = E.iloc[0], 0, 0
    for i, v in enumerate(E.values):
        if v >= peak_i:
            peak_i, cur_start = v, i
        longest = max(longest, i - cur_start)
    m = E.resample("ME").last()
    mr = m.pct_change().dropna()
    kcum = K.iloc[-1] / K.iloc[0] - 1
    kcagr = (1 + kcum) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    ex = r - rk
    out = {
        "누적%": cum * 100,
        "연복리%": cagr * 100,
        "연변동성%": vol * 100,
        "샤프(무위험0)": (r.mean() * 252) / vol if vol > 0 else np.nan,
        "최대손실폭%": dd.min() * 100,
        "최대손실기간(거래일)": longest,
        "최악의달%": mr.min() * 100 if len(mr) else np.nan,
        "월승률%": (mr > 0).mean() * 100 if len(mr) else np.nan,
        "코스피대비 연초과%": (cagr - kcagr) * 100,
        "정보비율(코스피)": (ex.mean() * 252) / (ex.std() * np.sqrt(252)) if ex.std() > 0 else np.nan,
    }
    if cost is not None:
        c = cost.iloc[-1] - cost.iloc[0]
        t = traded.iloc[-1] - traded.iloc[0]
        out["비용합계(원)"] = c
        out["연비용률%"] = c / E.mean() / yrs * 100
        out["연회전율(배)"] = t / E.mean() / yrs / 2  # 매수+매도 합의 절반 = 한 번 갈아탄 비율
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


def slice_(S: pd.Series, a: str, b: str) -> pd.Series:
    """a~b 구간. 첫날의 수익도 들어가게 전날 값을 기준점으로 붙인다."""
    idx = S.index
    i0 = max(idx.searchsorted(pd.Timestamp(a)) - 1, 0)
    i1 = idx.searchsorted(pd.Timestamp(b), side="right")
    return S.iloc[i0:i1]


PERIODS = {"전체 2020-02~2026-09": ("2020-02-01", "2026-12-31"), "2020-24": ("2020-02-01", "2024-12-31"), "2025-26": ("2025-01-01", "2026-12-31")}


def label(tag: str) -> str:
    name, w, tr = tag.split("_w")[0], tag.split("_w")[1].split("_")[0], "추세켬" if "_trend" in tag else "추세끔"
    kind = "룰" if name == "BASE" else "모델"
    nice = {"BASE": "7지표", "STACK_LOGIT": "로지스틱", "STACK_LDA": "판별분석(LDA)", "STACK_GBM": "GBM", "VOTE": "투표", "RANKAVG": "순위평균"}[name]
    return f"[{kind}] {nice} · 지수{w}% · {tr}"


if __name__ == "__main__":
    B = {p.stem: pd.read_csv(p, index_col=0, parse_dates=True) for p in sorted((OUT / "equity").glob("*.csv")) if "from2024" not in p.stem}
    days = next(iter(B.values())).index
    BM = benchmarks(days)
    K = BM["코스피(지수)"]
    rows, yrows = [], []
    for tag, E in B.items():
        for pn, (a, b) in PERIODS.items():
            rows.append({"엔진": "B 연구", "전략": label(tag), "구간": pn, **metrics(slice_(E.equity, a, b), slice_(K, a, b), slice_(E.cost, a, b), slice_(E.traded, a, b))})
        for y in range(2020, 2027):
            yrows.append({"전략": label(tag), "연도": y, **metrics(slice_(E.equity, f"{y}-01-01", f"{y}-12-31"), slice_(K, f"{y}-01-01", f"{y}-12-31"))})
    for nm, S in BM.items():
        for pn, (a, b) in PERIODS.items():
            rows.append({"엔진": "기준", "전략": nm, "구간": pn, **metrics(slice_(S, a, b), slice_(K, a, b))})
        for y in range(2020, 2027):
            yrows.append({"전략": nm, "연도": y, **metrics(slice_(S, f"{y}-01-01", f"{y}-12-31"), slice_(K, f"{y}-01-01", f"{y}-12-31"))})
    M = pd.DataFrame(rows)
    M.to_csv(OUT / "metrics.csv", index=False)
    Y = pd.DataFrame(yrows)
    Y.pivot(index="전략", columns="연도", values="누적%").round(1).to_csv(OUT / "metrics_yearly.csv")

    # ---------------- 그래프 ----------------
    fig, ax = plt.subplots(figsize=(12, 6.5))
    show = ["BASE_w20_trend", "BASE_w20_notrend", "BASE_w0_notrend", "STACK_GBM_w20_notrend", "STACK_LOGIT_w20_notrend", "VOTE_w20_notrend", "RANKAVG_w20_notrend"]
    for tag in show:
        ax.plot(B[tag].equity / 1e6, label=label(tag), lw=1.6 if tag.startswith("BASE") else 1.0)
    for nm, S in BM.items():
        ax.plot(S / 1e6, label=nm, lw=1.0, ls="--")
    ax.set_yscale("log")
    ax.set_ylabel("평가액(백만 원, 로그)")
    ax.set_title("일 단위 전수 시험 2020-02 ~ 2026-09 (엔진 B, 시작 1,000만 원)")
    ax.axvline(pd.Timestamp("2025-01-01"), color="gray", lw=0.8)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "equity_all.png", dpi=110)

    # ---------------- 엔진 A ----------------
    A = {}
    for d in sorted(FT.glob("a_*")):
        if (d / "equity.csv").exists() and (d / "meta.json").exists():
            e = pd.read_csv(d / "equity.csv", parse_dates=["date"]).set_index("date")
            A[d.name] = e
    arows = []
    for name, e in A.items():
        a, b = "2024-07-01", "2026-09-17"
        arows.append({"엔진": "A 봇 실코드", "전략": name, "구간": "2024-07~2026-09", **metrics(slice_(e.equity, a, b), slice_(K.reindex(e.index).ffill(), a, b), slice_(e.cost, a, b), slice_(e.traded, a, b))})
    for tag in ["BASE_w20_trend_from2024-07", "BASE_w20_notrend_from2024-07"]:
        e = pd.read_csv(OUT / "equity" / f"{tag}.csv", index_col=0, parse_dates=True)
        arows.append({"엔진": "B 연구(같은 날 시작)", "전략": tag, "구간": "2024-07~2026-09", **metrics(slice_(e.equity, "2024-07-01", "2026-09-17"), slice_(K, "2024-07-01", "2026-09-17"), slice_(e.cost, "2024-07-01", "2026-09-17"), slice_(e.traded, "2024-07-01", "2026-09-17"))})
    pd.DataFrame(arows).to_csv(OUT / "metrics_engineA.csv", index=False)

    fig, ax = plt.subplots(figsize=(12, 5.5))
    for name, e in A.items():
        ax.plot(e.equity.loc["2024-06-01":] / 1e6, label=f"A {name}")
    for tag in ["BASE_w20_trend_from2024-07", "BASE_w20_notrend_from2024-07"]:
        e = pd.read_csv(OUT / "equity" / f"{tag}.csv", index_col=0, parse_dates=True)
        ax.plot(e.equity / 1e6, ls="--", label=f"B {tag}")
    ax.set_ylabel("평가액(백만 원)")
    ax.set_title("엔진 A(봇 실코드) vs 엔진 B(연구 엔진) — 같은 날 1,000만 원 시작")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "equity_engineA_vs_B.png", dpi=110)
    print(json.dumps({"B 전략": len(B), "A 실행": list(A)}, ensure_ascii=False))

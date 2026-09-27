"""엔진 B: 연구 엔진 일 단위 시뮬레이션 (11 전수 테스트). 룰 기반과 모델 기반을 같은 규칙으로 돌린다.

실행: DATA=~/.qbot/research-data BOTDB=~/.qbot/research-data/fulltest/base.sqlite3 <연구 파이썬> research_sim.py
순위표는 07 연구(analyze.py)의 것을 그대로 쓴다.
  룰 기반: BASE(7지표 합성, 봇과 같은 규칙)
  모델 기반: STACK_LOGIT·STACK_LDA·STACK_GBM(매년 1월 과거로만 재학습), VOTE, RANKAVG
운용 규칙(06·07과 같음 + 봇의 지수·추세 규칙):
  월말 판단 → 다음 거래일 시가 체결, 체결오차 ±0.2%, 수수료 0.015%, 매도세 연도별, 10종목,
  종목당 예산 = 시가 평가액 × (1 − 지수비중) / 10, 1주 가격이 예산 이하인 종목만.
  지수비중 w>0: KODEX200(069500)을 목표 w로 두고, 월말 교체일에 w에서 5%p 넘게 벗어났으면 맞춘다(봇 R14).
  추세 필터: 코스피 월말 종가가 최근 10개월 평균 이하이면 종목 부분 전부 현금(지수 부분은 유지, 봇 R5).
결과: ../equity/<전략>.csv(일별), ../picks/<전략>.csv(월별 목표 종목)
"""

from __future__ import annotations

import os
import pathlib
import pickle
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "07-formula-ensemble/scripts"))
import analyze as AN  # noqa: E402 - 07 순위표·엔진(06)을 함께 불러온다

eng = AN.eng
C, O, V, cal, LASTP = eng.C, eng.O, eng.V, eng.cal, eng.LASTP
N, BAND = 10, 0.05
OUT = HERE.parent
CACHE = pathlib.Path(os.environ["DATA"]).expanduser() / "fulltest" / "b"
CACHE.mkdir(parents=True, exist_ok=True)
BOTDB = pathlib.Path(os.environ["BOTDB"]).expanduser()


def etf_prices() -> tuple[pd.Series, pd.Series]:
    c = sqlite3.connect(f"file:{BOTDB}?mode=ro", uri=True)
    b = pd.read_sql(
        "select trade_date, open, close from daily_bar b join instrument i on i.id=b.instrument_id "
        "where i.code='069500' and series_no=1",
        c,
        parse_dates=["trade_date"],
    ).set_index("trade_date")
    c.close()
    return b.open.reindex(cal), b.close.reindex(cal).ffill()


EO, EC = etf_prices()
Km = eng.Km


def trend_up(t: pd.Timestamp) -> bool:
    hist = Km[: t + pd.offsets.MonthEnd(0)]
    return bool(hist.iloc[-1] > hist.iloc[-10:].mean())


def simulate(scores: dict, start: str, end: str, cash0: float, w: float, trend: bool, top60: bool, cap):
    days = cal[(cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))]
    cash, hold, etf = float(cash0), {}, 0
    cost = traded = 0.0
    eq, picks = [], []
    seed = max(t for t in scores if t < days[0])
    pending = (seed, scores[seed])

    def px_open(c):
        return O.at[d, c] if (pd.notna(C.at[d, c]) and O.at[d, c] > 0) else LASTP.at[d, c]

    def tradable(c):
        return pd.notna(C.at[d, c]) and V.at[d, c] > 0 and O.at[d, c] > 0

    def sell(c):
        nonlocal cash, cost, traded
        n, _ = hold.pop(c)
        o = O.at[d, c] if pd.notna(C.at[d, c]) else LASTP.at[d, c]
        px = o * (1 - eng.SLIP)
        gross = n * px
        fee = gross * (eng.FEE + eng.tax(d))
        cash += gross - fee
        cost += fee + n * o * eng.SLIP
        traded += gross

    def buy(c, budget):
        nonlocal cash, cost, traded
        if c in hold or len(hold) >= N or not tradable(c):
            return
        px = O.at[d, c] * (1 + eng.SLIP)
        n = int(min(budget, cash) // (px * (1 + eng.FEE)))
        if n >= 1:
            cash -= n * px * (1 + eng.FEE)
            cost += n * px * eng.FEE + n * O.at[d, c] * eng.SLIP
            traded += n * px
            hold[c] = (n, px)

    def target(rank, budget):
        r = rank.head(60) if top60 else rank
        r = r[r.close <= budget]
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
        eo = EO.get(d)
        eq_open = cash + sum(n * px_open(c) for c, (n, _) in hold.items()) + (etf * eo if pd.notna(eo) else etf * EC.get(d))
        for c in [c for c in hold if pd.isna(C.at[d, c])]:  # 폐지되면 마지막 가격으로 정리
            sell(c)
        if pending is not None:
            t, rank = pending
            up = trend_up(t) if trend else True
            budget = eq_open * (1 - w) / N
            tg = target(rank, budget) if up else []
            picks.append((t, d, up, ",".join(tg)))
            for c in list(hold):
                if c not in tg and tradable(c):
                    sell(c)
            for c in tg:
                buy(c, budget)
            if w > 0 and pd.notna(eo) and eo > 0:
                cur = etf * eo / eq_open
                if abs(cur - w) > BAND:
                    goal = int(eq_open * w // (eo * (1 + eng.SLIP)))
                    if goal > etf:
                        px = eo * (1 + eng.SLIP)
                        n = int(min((goal - etf) * px * (1 + eng.FEE), cash) // (px * (1 + eng.FEE)))
                        if n > 0:
                            cash -= n * px * (1 + eng.FEE)
                            cost += n * px * eng.FEE + n * eo * eng.SLIP
                            traded += n * px
                            etf += n
                    elif goal < etf:
                        n = etf - goal
                        px = eo * (1 - eng.SLIP)
                        cash += n * px * (1 - eng.FEE)  # 상장지수펀드는 매도세 면제
                        cost += n * px * eng.FEE + n * eo * eng.SLIP
                        traded += n * px
                        etf -= n
            pending = None
        if d in scores:
            pending = (d, scores[d])
        stock = sum(n * (C.at[d, c] if pd.notna(C.at[d, c]) else LASTP.at[d, c]) for c, (n, _) in hold.items())
        eq.append((d, cash + stock + etf * EC.get(d), cash, etf * EC.get(d), len(hold), cost, traded))
    E = pd.DataFrame(eq, columns=["date", "equity", "cash", "etf", "n", "cost", "traded"]).set_index("date")
    P = pd.DataFrame(picks, columns=["asof", "trade_day", "trend_up", "codes"])
    return E, P


RULES = {"BASE": dict(top60=True, cap=None)}
MODELS = {
    "STACK_LOGIT": dict(top60=False, cap=None),
    "STACK_LDA": dict(top60=False, cap=None),
    "STACK_GBM": dict(top60=False, cap=None),
    "VOTE": dict(top60=False, cap=3),
    "RANKAVG": dict(top60=False, cap=None),
}


def ranks() -> dict:
    f = CACHE / "ranks.pkl"
    if f.exists():
        return pickle.load(open(f, "rb"))
    R = {"BASE": AN.ranks_single("BASE"), "VOTE": AN.ranks_vote(), "RANKAVG": AN.ranks_avg()}
    log: list = []
    for k in ["LOGIT", "LDA", "GBM"]:
        R[f"STACK_{k}"] = AN.ranks_stack(k, log)
    pickle.dump(R, open(f, "wb"))
    return R


if __name__ == "__main__":
    CASH = 10_000_000
    START, END = "2020-02-01", str(cal[-1].date())
    R = ranks()
    (OUT / "equity").mkdir(exist_ok=True)
    (OUT / "picks").mkdir(exist_ok=True)
    runs = []
    for name, kw in {**RULES, **MODELS}.items():
        for w in (0.0, 0.2):
            for trend in (False, True):
                runs.append((name, w, trend, kw, START))
    # 봇(엔진 A)과 같은 날 같은 돈으로 시작하는 교차 검증용
    runs.append(("BASE", 0.2, True, RULES["BASE"], "2024-07-01"))
    runs.append(("BASE", 0.2, False, RULES["BASE"], "2024-07-01"))
    for name, w, trend, kw, start in runs:
        tag = f"{name}_w{int(w * 100)}_{'trend' if trend else 'notrend'}" + ("_from2024-07" if start != START else "")
        if (OUT / "equity" / f"{tag}.csv").exists():
            continue
        E, P = simulate(R[name], start, END, CASH, w, trend, **kw)
        E.to_csv(OUT / "equity" / f"{tag}.csv")
        P.to_csv(OUT / "picks" / f"{tag}.csv", index=False)
        print(tag, f"{E.equity.iloc[-1]:,.0f}", flush=True)
    print("끝", START, END)

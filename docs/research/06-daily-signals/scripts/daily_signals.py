"""매일 신호 백테스트: 실적 공시 신선도 반영(C), 종목별 손절(D), 둘의 조합(E).

05-paper-run-1m의 build.py·simulate.py와 같은 자료·규칙·비용을 쓴다. 다른 점은 둘이다.
  1. 점수를 월말만이 아니라 매 거래일 계산한다(공시가 들어온 날과 손절 확인에 필요).
  2. 재무 공개일을 두 가지로 돌린다.
     - assumed: 검증 때의 가정(결산일+95일, 분기말+50일/4분기 +95일)
     - actual : 2023년 이후 공시는 봇 DB의 실제 접수일, 그 전은 가정

실행: DATA=<bt·sim 폴더가 있는 곳> BOTDB=~/.qbot/qbot-paper.sqlite3 python daily_signals.py
결과: ../results.csv, ../equity/*.csv
"""

from __future__ import annotations

import os
import pathlib
import pickle
import re
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA = pathlib.Path(os.environ["DATA"])
BOTDB = pathlib.Path(os.environ.get("BOTDB", pathlib.Path.home() / ".qbot/qbot-paper.sqlite3"))
OUT = pathlib.Path(__file__).resolve().parent.parent
W = {"EP": 1, "SP": 1, "ROE": 1, "OPG": 1, "OPG_Q": 1, "VOL60": -1, "TURN20": -1}  # rules.json
N, KEEP = 10, 20  # 보유 수, 공시 교체에서 "밀려났다"로 보는 순위
FEE, SLIP = 0.00015, 0.002
TAX_BY_YEAR = {2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018, 2025: 0.0015, 2026: 0.0020}
PERIODS = {"검증 2020-02~2024-12": ("2020-02-01", "2024-12-31"), "표본외 2025-01~2026-09": ("2025-01-01", "2026-09-01")}

# ---------------- 자료 ----------------
O, C, V = pickle.load(open(DATA / "sim/ocv.pkl", "rb"))
cal = C.index
L = pd.read_csv(DATA / "bt/listing.csv", dtype={"Code": str}).set_index("Code")
imp = pd.read_csv(DATA / "bt/implied_shares.csv", dtype={"code": str}).set_index("code")
sh = L.Stocks.reindex(C.columns).astype(float).fillna(imp.sh.reindex(C.columns))
MC = C * sh
TV = C * V
R = C.pct_change()
VOL60 = R.rolling(60).std()
TV20 = TV.rolling(20).mean()
TURN20 = TV.rolling(20).sum() / MC
LASTP = C.ffill()  # 폐지·정지 종목의 마지막 가격
KS = pickle.load(open(DATA / "bt/macro.pkl", "rb"))["KOSPI"]
Km = KS.resample("ME").last()

fs = pd.read_csv(DATA / "bt/fs.csv", dtype={"code": str}, parse_dates=["fy_end", "avail"]).sort_values(["code", "fy_end"])
fs["op_g"] = fs.groupby("code").op.pct_change().where(fs.groupby("code").op.shift() > 0)
Q = pd.read_pickle(DATA / "bt/qop.pkl")
Q["avail"] = Q.qend + pd.to_timedelta(Q.lag, unit="D")


def actual_dates() -> pd.DataFrame:
    """봇 DB 공시 → (code, 기간말, 최초 접수일). 정정 공시는 뺀다(처음 공개된 날이 기준)."""
    c = sqlite3.connect(f"file:{BOTDB}?mode=ro", uri=True)
    rows = c.execute(
        "select i.code, f.report_kind, f.title, f.rcept_date from filing f join instrument i on i.id=f.instrument_id "
        "where f.report_kind != 'correction'"
    ).fetchall()
    c.close()
    out = []
    for code, kind, title, d in rows:
        m = re.search(r"\((\d{4})\.(\d{2})\)", title or "")
        if not m:
            continue
        pend = pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0)
        out.append((code, kind, pend, pd.Timestamp(d)))
    df = pd.DataFrame(out, columns=["code", "kind", "pend", "rcept"])
    return df.groupby(["code", "kind", "pend"]).rcept.min().reset_index()


ACT = actual_dates()


def with_dates(which: str):
    """재무 표 두 개에 그 방식의 공개일(avail)을 붙여 돌려준다."""
    f, q = fs.copy(), Q.copy()
    if which == "actual":
        ann = ACT[ACT.kind == "annual"].set_index(["code", "pend"]).rcept
        anyk = ACT.groupby(["code", "pend"]).rcept.min()
        fa = ann.reindex(pd.MultiIndex.from_arrays([f.code, f.fy_end])).values
        f["avail"] = pd.Series(fa, index=f.index).fillna(f.avail)
        qa = anyk.reindex(pd.MultiIndex.from_arrays([q.code, q.qend])).values
        q["avail"] = pd.Series(qa, index=q.index).fillna(q.avail)
    return f.sort_values("avail"), q.sort_values("avail")


# ---------------- 매일 점수 ----------------
def daily_scores(f: pd.DataFrame, q: pd.DataFrame, days) -> tuple[dict, dict]:
    """날짜마다 (순위표, 그날 새로 공개된 종목). 순위표는 code 인덱스, close·score, 점수 내림차순."""
    scores, fresh = {}, {}
    fav = f.avail.values.astype("datetime64[ns]")
    qav = q.avail.values.astype("datetime64[ns]")
    prev = None
    for t in days:
        i = cal.get_loc(t)
        if i < 252:
            continue
        tn = np.datetime64(t)
        fi = f.iloc[: np.searchsorted(fav, tn, side="right")].drop_duplicates("code", keep="last").set_index("code")
        fi = fi[(t - fi.fy_end).dt.days <= 500]
        qi = q.iloc[: np.searchsorted(qav, tn, side="right")].drop_duplicates("code", keep="last").set_index("code")
        qi = qi[(t - qi.avail).dt.days <= 150]
        c, mc = C.iloc[i], MC.iloc[i]
        d = pd.DataFrame({"close": c, "mcap": mc, "tv20": TV20.iloc[i], "traded": V.iloc[i] > 0})
        d["VOL60"], d["TURN20"] = VOL60.iloc[i], TURN20.iloc[i]
        d["EP"] = fi.ni_use.reindex(d.index) / mc
        d["SP"] = fi.rev.reindex(d.index) / mc
        d["ROE"] = (fi.ni_use / fi.equity_use.where(fi.equity_use > 0)).reindex(d.index)
        d["OPG"] = fi.op_g.reindex(d.index).clip(-1, 5)
        d["OPG_Q"] = qi.g.reindex(d.index)
        d = d[(d.close >= 1000) & (d.tv20 >= 5e8) & d.traded & (d.mcap >= 5e10)].dropna(subset=list(W))
        s = sum(sg * d[k].rank(pct=True) for k, sg in W.items())
        scores[t] = d.assign(score=s).sort_values("score", ascending=False)[["close", "score"]]
        if prev is not None:  # (전 거래일, 오늘]에 공개된 종목
            pn = np.datetime64(prev)
            nf = set(f.code[(fav > pn) & (fav <= tn)]) | set(q.code[(qav > pn) & (qav <= tn)])
            fresh[t] = nf
        prev = t
    return scores, fresh


# ---------------- 운용 ----------------
def tax(d) -> float:
    return TAX_BY_YEAR.get(d.year, 0.0020)


def simulate(scores, fresh, start, end, cash0, trend=False, event=False, stop=None, refill=False):
    days = cal[(cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))]
    mends = set(pd.Series(cal, index=cal).resample("ME").last().values)
    cash, hold, cost, trades = float(cash0), {}, 0.0, 0
    eq = []
    pending: dict[str, str] = {}  # 오늘 시가에 할 일 {"monthly"|"event"|"stop": payload}
    stopped_this_month: set[str] = set()
    last_rank = None
    lastp = LASTP
    seed = max(t for t in scores if t < days[0] and np.datetime64(t) in mends)  # 시작 직전 월말 판단으로 첫날 산다
    hist = Km[: seed + pd.offsets.MonthEnd(0)]
    pending["monthly"] = (scores[seed], bool(hist.iloc[-1] > hist.iloc[-10:].mean()))

    def value(d):
        return cash + sum(n * (C.at[d, c] if pd.notna(C.at[d, c]) else lastp.at[d, c]) for c, (n, _, _) in hold.items())

    def sell(d, c):
        nonlocal cash, cost, trades
        n, bp, bd = hold.pop(c)
        o = O.at[d, c] if pd.notna(C.at[d, c]) else lastp.at[d, c]  # 폐지 뒤면 마지막 가격으로 정리
        px = o * (1 - SLIP)
        gross = n * px
        fee = gross * (FEE + tax(d))
        cash += gross - fee
        cost += fee + n * o * SLIP
        trades += 1

    def tradable(d, c):
        return pd.notna(C.at[d, c]) and V.at[d, c] > 0 and O.at[d, c] > 0

    def buy(d, c, eq_open):
        nonlocal cash, cost, trades
        if c in hold or len(hold) >= N or not tradable(d, c):
            return
        px = O.at[d, c] * (1 + SLIP)
        n = int(min(eq_open / N, cash) // (px * (1 + FEE)))
        if n >= 1:
            cash -= n * px * (1 + FEE)
            cost += n * px * FEE + n * O.at[d, c] * SLIP
            hold[c] = (n, px * (1 + FEE), d)
            trades += 1

    def eligible(rank, eq_open):
        return rank.index[rank.close <= eq_open / N].tolist()

    for d in days:
        eq_open = cash + sum(
            n * (O.at[d, c] if (pd.notna(C.at[d, c]) and O.at[d, c] > 0) else lastp.at[d, c]) for c, (n, _, _) in hold.items()
        )
        # 폐지된 종목은 그날 정리
        for c in [c for c in hold if pd.isna(C.at[d, c])]:
            sell(d, c)
        job = pending.pop("monthly", None)
        if job is not None:
            rank, up = job
            last_rank, stopped_this_month = rank, set()
            target = eligible(rank.head(60), eq_open)[:N] if (up or not trend) else []
            for c in list(hold):
                if c not in target and tradable(d, c):
                    sell(d, c)
            for c in target:
                buy(d, c, eq_open)
        job = pending.pop("event", None)
        if job is not None and last_rank is not None:
            rank, codes = job
            order = eligible(rank, eq_open)
            pos = {c: k for k, c in enumerate(order)}
            for c in list(hold):  # 새 공시가 난 보유 종목이 20위 밖으로 밀렸으면 판다
                if c in codes and pos.get(c, 10**9) >= KEEP and tradable(d, c):
                    sell(d, c)
            for c in [c for c in order[:N] if c in codes and c not in hold]:  # 새 공시로 10위 안에 들어온 종목
                if len(hold) >= N:  # 자리가 없으면 10위 밖의 가장 낮은 보유 종목과 바꾼다
                    worst = max(hold, key=lambda h: pos.get(h, 10**9))
                    if pos.get(worst, 10**9) < N or not tradable(d, worst):
                        break
                    sell(d, worst)
                buy(d, c, eq_open)
        job = pending.pop("stop", None)
        if job is not None:
            for c in job:
                if c in hold and tradable(d, c):
                    sell(d, c)
                    stopped_this_month.add(c)
            if refill and last_rank is not None:
                for c in eligible(last_rank.head(60), eq_open):
                    if len(hold) >= N:
                        break
                    if c not in stopped_this_month:
                        buy(d, c, eq_open)
        # ---- 장 마감 뒤: 내일 할 일을 정한다 ----
        nxt = cal[cal > d]
        if len(nxt) and d in scores:
            if np.datetime64(d) in mends:
                hist = Km[: d + pd.offsets.MonthEnd(0)]
                pending["monthly"] = (scores[d], bool(hist.iloc[-1] > hist.iloc[-10:].mean()))
            elif event and fresh.get(d):
                pending["event"] = (scores[d], fresh[d])
        if stop is not None:
            hit = [c for c, (n, bp, _) in hold.items() if pd.notna(C.at[d, c]) and C.at[d, c] <= bp * (1 - stop)]
            if hit:
                pending["stop"] = hit
        eq.append((d, value(d), len(hold)))
    E = pd.DataFrame(eq, columns=["date", "equity", "n"]).set_index("date")
    return E, cost, trades


def etf_series(days, cash0):
    c = sqlite3.connect(f"file:{BOTDB}?mode=ro", uri=True)
    b = pd.read_sql(
        "select trade_date, open, close from daily_bar b join instrument i on i.id=b.instrument_id "
        "where i.code='069500' and series_no=1",
        c,
        parse_dates=["trade_date"],
    ).set_index("trade_date")
    c.close()
    o0 = b.open.reindex(days).iloc[0]
    n = int(cash0 // (o0 * (1 + FEE)))
    return n * b.close.reindex(days).ffill() + (cash0 - n * o0 * (1 + FEE))


def metrics(E: pd.Series, cash0: float) -> dict:
    yrs = (E.index[-1] - E.index[0]).days / 365.25
    m = E.resample("ME").last()
    mr = m.pct_change()
    mr.iloc[0] = m.iloc[0] / cash0 - 1
    return {
        "연복리%": round(((E.iloc[-1] / cash0) ** (1 / yrs) - 1) * 100, 1),
        "누적%": round((E.iloc[-1] / cash0 - 1) * 100, 1),
        "최대 손실폭%": round((E / E.cummax() - 1).min() * 100, 1),
        "최악의 달%": round(mr.min() * 100, 1),
    }


SCENARIOS = [
    ("A 기준(월말만)", {}),
    ("B 추세 필터", {"trend": True}),
    ("C 공시 반영", {"event": True}),
    ("D 손절 -10% (월말까지 현금)", {"stop": 0.10}),
    ("D 손절 -15% (월말까지 현금)", {"stop": 0.15}),
    ("D 손절 -20% (월말까지 현금)", {"stop": 0.20}),
    ("D 손절 -10% (다음 순위로 채움)", {"stop": 0.10, "refill": True}),
    ("D 손절 -15% (다음 순위로 채움)", {"stop": 0.15, "refill": True}),
    ("D 손절 -20% (다음 순위로 채움)", {"stop": 0.20, "refill": True}),
    ("E 공시 반영 + 손절 -15% (채움)", {"event": True, "stop": 0.15, "refill": True}),
]

if __name__ == "__main__":
    CASH = 1_000_000
    all_days = cal[(cal >= "2019-12-01") & (cal <= "2026-09-01")]
    rows = []
    (OUT / "equity").mkdir(exist_ok=True)
    for dates in ["actual", "assumed"]:
        f, q = with_dates(dates)
        scores, fresh = daily_scores(f, q, all_days)
        print(dates, "점수 계산", len(scores), "일", flush=True)
        for label, kw in SCENARIOS:
            if dates == "assumed" and not label.startswith(("A", "C")):
                continue  # 가정 공개일은 공시 반영(C)과 기준(A)만 비교한다
            for pname, (s, e) in PERIODS.items():
                E, cost, trades = simulate(scores, fresh, s, e, CASH, **kw)
                days = E.index
                yrs = (days[-1] - days[0]).days / 365.25
                Es, _, _ = simulate(scores, fresh, s, e, CASH * 0.8, **kw)
                blend = Es.equity + etf_series(days, CASH * 0.2)
                row = {"공개일": dates, "시나리오": label, "기간": pname, **metrics(E.equity, CASH)}
                row["연 매매 건수"] = round(trades / yrs, 1)
                row["비용 합계(원)"] = round(cost)
                row["연 비용률%"] = round(cost / E.equity.mean() / yrs * 100, 2)
                bm = metrics(blend, CASH)
                row["80:20 연복리%"], row["80:20 최대 손실폭%"] = bm["연복리%"], bm["최대 손실폭%"]
                rows.append(row)
                tag = f"{dates}_{label.split()[0]}_{kw.get('stop', '')}{'r' if kw.get('refill') else ''}_{s[:4]}"
                E.to_csv(OUT / "equity" / f"{tag}.csv")
                print(row, flush=True)
    pd.DataFrame(rows).to_csv(OUT / "results.csv", index=False)

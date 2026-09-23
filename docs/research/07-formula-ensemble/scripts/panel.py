"""월말마다 공식 점수와 다음 달 수익률을 만든다(07 연구). 결과는 CACHE/panel.pkl.

실행: DATA=~/.qbot/research-data <연구용 파이썬> panel.py
06 엔진(daily_signals.py)의 자료·BASE 계산을 그대로 가져다 쓴다. BASE가 06과 같아야 비교가 공정하다.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ["DATA"]).expanduser()
CACHE = DATA / "07"
CACHE.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location("eng", HERE.parents[1] / "06-daily-signals/scripts/daily_signals.py")
eng = importlib.util.module_from_spec(spec)
sys.modules["eng"] = eng
spec.loader.exec_module(eng)

C, V, MC, cal = eng.C, eng.V, eng.MC, eng.cal
LASTP = eng.LASTP
MENDS = pd.Series(cal, index=cal).resample("ME").last()
MENDS = pd.DatetimeIndex(MENDS[(MENDS >= "2019-12-01") & (MENDS <= "2026-08-31")].values)


# ---------------- 넓힌 재무 ----------------
def load_fs_ext() -> pd.DataFrame:
    f = pd.read_csv(DATA / "bt/fs_ext.csv", dtype={"code": str}, parse_dates=["fy_end", "avail"])
    f = f.sort_values(["code", "fy_end"])
    g = f.groupby("code")
    gap = (f.fy_end - g.fy_end.shift()).dt.days
    ok = gap.between(330, 400)  # 바로 앞 결산만 전년으로 본다
    for col in ["assets", "ni_use", "cur_assets", "cur_liab", "noncur_liab", "gross_use", "rev", "issued_cap", "implied_shares"]:
        f[col + "_p"] = g[col].shift().where(ok)
    return f.sort_values("avail").reset_index(drop=True)


def load_q() -> pd.DataFrame:
    q = pd.read_pickle(DATA / "bt/qfs_ext.pkl")
    return q.sort_values("avail").reset_index(drop=True)


FS = load_fs_ext()
QF = load_q()
F06, Q06 = eng.with_dates("actual")  # BASE용(06과 같은 자료)
IND = FS.drop_duplicates("code", keep="last").set_index("code").industry_latest


def pct(s: pd.Series, sign: int = 1) -> pd.Series:
    return (sign * s).rank(pct=True)


def month_panel(t: pd.Timestamp) -> pd.DataFrame:
    i = cal.get_loc(t)
    tn = np.datetime64(t)
    # --- BASE (06 daily_scores와 같은 계산) ---
    fav = F06.avail.values.astype("datetime64[ns]")
    qav = Q06.avail.values.astype("datetime64[ns]")
    fi = F06.iloc[: np.searchsorted(fav, tn, side="right")].drop_duplicates("code", keep="last").set_index("code")
    fi = fi[(t - fi.fy_end).dt.days <= 500]
    qi = Q06.iloc[: np.searchsorted(qav, tn, side="right")].drop_duplicates("code", keep="last").set_index("code")
    qi = qi[(t - qi.avail).dt.days <= 150]
    c, mc = C.iloc[i], MC.iloc[i]
    d = pd.DataFrame({"close": c, "mcap": mc, "tv20": eng.TV20.iloc[i], "traded": V.iloc[i] > 0})
    d["VOL60"], d["TURN20"] = eng.VOL60.iloc[i], eng.TURN20.iloc[i]
    d["EP"] = fi.ni_use.reindex(d.index) / mc
    d["SP"] = fi.rev.reindex(d.index) / mc
    d["ROE"] = (fi.ni_use / fi.equity_use.where(fi.equity_use > 0)).reindex(d.index)
    d["OPG"] = fi.op_g.reindex(d.index).clip(-1, 5)
    d["OPG_Q"] = qi.g.reindex(d.index)
    d = d[(d.close >= 1000) & (d.tv20 >= 5e8) & d.traded & (d.mcap >= 5e10)].copy()
    full = d.dropna(subset=list(eng.W))
    base = sum(sg * full[k].rank(pct=True) for k, sg in eng.W.items())
    d["BASE"] = base.reindex(d.index)

    # --- 넓힌 재무 ---
    xav = FS.avail.values.astype("datetime64[ns]")
    x = FS.iloc[: np.searchsorted(xav, tn, side="right")].drop_duplicates("code", keep="last").set_index("code")
    x = x[(t - x.fy_end).dt.days <= 500].reindex(d.index)
    d["fin"] = x.fin_kind.notna()
    d["ind"] = IND.reindex(d.index)
    A = x.assets.where(x.assets > 0)
    Ap = x.assets_p.where(x.assets_p > 0)
    avgA = pd.concat([A, Ap], axis=1).mean(axis=1)
    d["GPA"] = x.gross_use / A
    roa, roa_p = x.ni_use / Ap, x.ni_use_p / (x.assets_p)  # 원식: 기초 자산. 전년 ROA는 그 해 자산으로 근사
    cfo_a = x.cfo / A
    lev, lev_p = x.noncur_liab / A, x.noncur_liab_p / Ap
    cr, cr_p = x.cur_assets / x.cur_liab.where(x.cur_liab > 0), x.cur_assets_p / x.cur_liab_p.where(x.cur_liab_p > 0)
    gm, gm_p = x.gross_use / x.rev.where(x.rev > 0), x.gross_use_p / x.rev_p.where(x.rev_p > 0)
    at, at_p = x.rev / A, x.rev_p / Ap
    sig = [
        roa > 0,
        x.cfo > 0,
        roa > roa_p,
        cfo_a > (x.ni_use / A),
        lev < lev_p,
        cr > cr_p,
        x.issued_cap <= x.issued_cap_p,
        gm > gm_p,
        at > at_p,
    ]
    fs_ = sum(s.fillna(False).astype(int) for s in sig)
    d["FSCORE"] = fs_.where(x.assets.notna())
    sh_ratio = (x.implied_shares / x.implied_shares_p).where(x.implied_shares_p > 0)
    cap_ratio = (x.issued_cap / x.issued_cap_p).where(x.issued_cap_p > 0)
    d["NSI"] = np.log(sh_ratio.fillna(cap_ratio).where(lambda s: s > 0))
    d["AG"] = A / Ap - 1
    d["ACC"] = (x.ni_use - x.cfo) / avgA
    ev = d.mcap + x.debt.fillna(0) - x.cash.fillna(0)
    d["EY"] = (x.op / ev.where(ev > 0))
    tl = x.liab.where(x.liab > 0)
    d["ZPP"] = (
        6.56 * (x.cur_assets - x.cur_liab) / A + 3.26 * x.retained / A + 6.72 * x.op / A + 1.05 * x.equity / tl
    )
    d["ROE_A"] = x.ni_use / x.equity_use.where(x.equity_use > 0)
    d["DE"] = x.liab / x.equity.where(x.equity > 0)
    # 추가 특성(업종 판별용)
    d["GM"] = gm
    d["OM"] = x.op / x.rev.where(x.rev > 0)
    d["ATURN"] = at
    d["CURR"] = cr
    d["CASHA"] = x.cash / A
    d["CAPEXA"] = x.capex_abs / A

    # --- 분기 12개: 버핏 품질 ---
    qav2 = QF.avail.values.astype("datetime64[ns]")
    qq = QF.iloc[: np.searchsorted(qav2, tn, side="right")]
    qq = qq[qq.code.isin(d.index)].sort_values(["code", "qend"]).drop_duplicates(["code", "qend"], keep="last")
    qq = qq.groupby("code").tail(12)
    qq = qq.assign(m=qq.op_q / qq.rev_q.where(qq.rev_q > 0), pos=(qq.ni_q > 0).astype(float))
    agg = qq.groupby("code").agg(n=("m", "count"), msd=("m", "std"), pos=("pos", "mean"), npos=("ni_q", "count"))
    d["MSD"] = agg.msd.where(agg.n >= 8).reindex(d.index)
    d["POS"] = agg.pos.where(agg.npos >= 8).reindex(d.index)

    # --- 다음 달 수익률 ---
    k = MENDS.get_loc(t)
    if k + 1 < len(MENDS):
        t1 = MENDS[k + 1]
        d["fwd"] = LASTP.loc[t1].reindex(d.index) / d.close - 1
    else:
        d["fwd"] = np.nan
    d["date"] = t
    return d


def buffett(d: pd.DataFrame) -> pd.Series:
    parts = [pct(d.ROE_A), pct(d.POS), pct(d.MSD, -1), pct(d.DE, -1)]
    return pd.concat(parts, axis=1).mean(axis=1, skipna=False)


if __name__ == "__main__":
    out = CACHE / "panel.pkl"
    rows = []
    for t in MENDS:
        p = month_panel(t)
        p["BUFFETT"] = buffett(p[~p.fin])
        rows.append(p.reset_index(names="code"))
        print(t.date(), len(p), int(p.BASE.notna().sum()), flush=True)
    P = pd.concat(rows, ignore_index=True)
    P.to_pickle(out)
    print("저장", out, P.shape)

"""엔진 A(봇 실코드)와 B(연구 엔진)의 교차 검증. 같은 전략(7지표+추세+지수20%)을 같은 날 시작했다.

실행: <봇 파이썬> crosscheck.py
1) 월별 목표 종목 일치(10종목 중 몇 개 같나)
2) 어긋난 종목마다 7개 지표를 봇(compute_factors)과 연구(07 panel) 값으로 나란히 놓고 어느 지표가 다른지 센다
3) 일별 평가액 차이
결과: ../crosscheck_picks.csv, ../crosscheck_factors.csv, ../crosscheck_summary.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import app.core.models_registry  # noqa: E402,F401
from app.core.db import make_engine, make_session_factory  # noqa: E402
from app.core.pit import PointInTime  # noqa: E402
from app.domains.decision import scoring  # noqa: E402
from app.domains.marketdata.service import MarketDataService  # noqa: E402

OUT = Path(__file__).resolve().parent.parent
FT = Path.home() / ".qbot/research-data/fulltest"
FACT = ["EP", "SP", "ROE", "OPG", "OPG_Q", "VOL60", "TURN20"]


def main(a_run: str = "a_trend_fix", b_tag: str = "BASE_w20_trend_from2024-07") -> None:
    A = pd.read_csv(FT / a_run / "decisions.csv", dtype={"code": str})
    A = A.groupby("asof").code.apply(list)
    B = pd.read_csv(OUT / "picks" / f"{b_tag}.csv", dtype={"codes": str})
    B["asof"] = pd.to_datetime(B["asof"]).dt.strftime("%Y-%m-%d")
    B = B.set_index("asof")
    # 07 panel(연구 엔진 지표)을 csv로 풀어 둔 것. 봇 가상환경에는 pyarrow가 없어 pickle을 못 읽는다
    P = pd.read_csv(FT / "panel_factors.csv", dtype={"code": str}, parse_dates=["date"])
    s = make_session_factory(make_engine(FT / a_run / "sim.sqlite3"))()
    md = MarketDataService(s)

    rows, frows = [], []
    for asof in sorted(set(A.index) | set(B.index)):
        a = A.get(asof, [])
        b = [c for c in str(B.codes.get(asof, "") or "").split(",") if c and c != "nan"]
        up = bool(B.trend_up.get(asof, True)) if asof in B.index else None
        rows.append({"asof": asof, "봇": len(a), "연구": len(b), "같은 종목": len(set(a) & set(b)), "추세 상승(연구)": up,
                     "봇만": ",".join(sorted(set(a) - set(b))), "연구만": ",".join(sorted(set(b) - set(a)))})
        diff = (set(a) ^ set(b))
        if not diff or asof not in set(P.date.dt.strftime("%Y-%m-%d")):
            continue
        pit = PointInTime(date.fromisoformat(asof))
        fa = scoring.compute_factors(md.bars(pit, 252), md.financials(pit), md.shares())
        fb = P[P.date == pd.Timestamp(asof)].set_index("code")
        for c in sorted(diff):
            row = {"asof": asof, "code": c, "쪽": "봇만" if c in a else "연구만"}
            for f in FACT:
                va = fa[f].get(c, np.nan) if f in fa else np.nan
                vb = fb[f].get(c, np.nan) if f in fb else np.nan
                row[f"{f}_봇"], row[f"{f}_연구"] = va, vb
                if pd.isna(va) and pd.isna(vb):
                    row[f"{f}_다름"] = False
                elif pd.isna(va) or pd.isna(vb):
                    row[f"{f}_다름"] = True
                else:
                    row[f"{f}_다름"] = abs(va - vb) > 0.02 * max(abs(va), abs(vb), 1e-9)
            frows.append(row)
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "crosscheck_picks.csv", index=False)
    F = pd.DataFrame(frows)
    F.to_csv(OUT / "crosscheck_factors.csv", index=False)
    ea = pd.read_csv(FT / a_run / "equity.csv", parse_dates=["date"]).set_index("date").equity
    eb = pd.read_csv(OUT / "equity" / f"{b_tag}.csv", index_col=0, parse_dates=True).equity
    j = pd.concat([ea, eb], axis=1, keys=["A", "B"]).dropna().loc["2024-07-01":]
    ra, rb = j.A.pct_change().dropna(), j.B.pct_change().dropna()
    summary = {
        "월 수": len(R),
        "평균 일치(10종목 중)": round(float(R["같은 종목"].mean()), 2),
        "완전 일치 달": int((R["같은 종목"] == R[["봇", "연구"]].max(axis=1)).sum()),
        "일 수익률 상관": round(float(ra.corr(rb)), 4),
        "추적오차(연%)": round(float((ra - rb).std() * np.sqrt(252) * 100), 2),
        "끝 평가액 A": int(j.A.iloc[-1]),
        "끝 평가액 B": int(j.B.iloc[-1]),
        "지표별 다름(어긋난 종목 중)": {f: int(F[f"{f}_다름"].sum()) for f in FACT} if len(F) else {},
        "어긋난 종목 수": len(F),
    }
    json.dump(summary, open(OUT / f"crosscheck_summary_{a_run}.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(R.to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])

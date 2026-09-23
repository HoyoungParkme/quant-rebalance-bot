"""사후 진단(사전 고정 목록 밖). 채택 판단에는 쓰지 않는다 — 결과를 해석하려고 본다.

1. BASE에 걸러내기(Z″ 하위 10%, 자산성장 상위 20%·하위 10%)만 적용하면 어떤가
2. 단일 공식 상위 10종목은 어떤 종목인가(시가총액·주가 중앙값)
실행: DATA=~/.qbot/research-data <연구용 파이썬> posthoc.py
"""

from __future__ import annotations

import pandas as pd

import analyze as A

CASH = 1_000_000
rows = []
flt = {t: A.order(A.filtered(g), A.filtered(g).BASE) for t, g in A.FR.items()}
for pname, (s, e) in A.PERIODS.items():
    E, cost, tr = A.simulate(flt, s, e, CASH, top60=True, cap=None)
    rows.append({"전략": "BASE+걸러내기(사후)", "기간": pname, **A.eng.metrics(E.equity, CASH)})
pd.DataFrame(rows).to_csv(A.OUT / "posthoc_base_filtered.csv", index=False)
print(pd.DataFrame(rows).to_string())

prof = []
for name in A.SINGLES:
    r = A.ranks_single(name)
    mc, px = [], []
    for t, rk in r.items():
        top = rk[rk.close <= 100_000].head(10).index
        g = A.FR[t]
        mc.append(g.mcap.reindex(top).median())
        px.append(g.close.reindex(top).median())
    prof.append({"공식": name, "상위10 시총 중앙값(억)": round(pd.Series(mc).median() / 1e8), "상위10 주가 중앙값": round(pd.Series(px).median())})
pd.DataFrame(prof).to_csv(A.OUT / "posthoc_top10_profile.csv", index=False)
print(pd.DataFrame(prof).to_string())

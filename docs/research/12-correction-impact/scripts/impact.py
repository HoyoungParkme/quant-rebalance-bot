"""정정 영향 측정 3단계: 숫자가 바뀐 표본이 ROE 백분위를 몇 칸 옮기나.

EP도 순이익으로 움직이지만 시가총액이 같으므로 ROE와 비슷한 크기다. 매출·영업이익은 표본에서 바뀐 적이 없어
SP·OPG·OPG_Q는 영향이 없다. 같은 기간(period_end·period_kind)의 봇 DB 전 종목 ROE 분포에서 순위를 본다.
"""

import csv
import pathlib
import sqlite3

import numpy as np

HOME = pathlib.Path.home()
OUT = pathlib.Path(__file__).resolve().parent.parent
c = sqlite3.connect(f"file:{HOME}/.qbot/qbot-paper.sqlite3?mode=ro", uri=True)
rows = [r for r in csv.DictReader(open(OUT / "compare.csv")) if r["status"] == "ok"]
res = []
for r in rows:
    ni_rel = float(r["net_income_rel"]) if r.get("net_income_rel") else 0.0
    eq_rel = float(r["equity_rel"]) if r.get("equity_rel") else 0.0
    if ni_rel <= 0.005 and eq_rel <= 0.005:
        continue
    fid = c.execute("select id from filing where rcept_no=?", (r["rcept_no"],)).fetchone()[0]
    pe, pk = c.execute("select period_end, period_kind from financial_snapshot where filing_id=? limit 1", (fid,)).fetchone()
    pool = np.array(
        [n / e for n, e in c.execute(
            "select net_income, equity from financial_snapshot where period_end=? and period_kind=? and equity>0 and net_income is not null",
            (pe, pk))]
    )
    ni_db, eq_db = float(r["net_income_db"] or 0), float(r["equity_db"] or 0)
    ni_doc = float(r["net_income_doc"]) if r.get("net_income_doc") else ni_db
    eq_doc = float(r["equity_doc"]) if r.get("equity_doc") else eq_db
    if eq_db <= 0 or eq_doc <= 0 or len(pool) < 100:
        continue
    p_db = (pool < ni_db / eq_db).mean() * 100
    p_doc = (pool < ni_doc / eq_doc).mean() * 100
    res.append(dict(rcept_no=r["rcept_no"], gap_band=r["gap_band"], period=pe, n_pool=len(pool),
                    roe_db=round(ni_db / eq_db, 4), roe_doc=round(ni_doc / eq_doc, 4),
                    pct_db=round(p_db, 1), pct_doc=round(p_doc, 1), shift=round(p_db - p_doc, 1)))
with open(OUT / "impact_roe.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(res[0]))
    w.writeheader()
    w.writerows(res)
for x in res:
    print(x)
print("백분위 이동 절대값 중앙값", np.median([abs(x["shift"]) for x in res]), "최대", max(abs(x["shift"]) for x in res))

"""09 표본 추출: 업종이 겹치지 않게 100종목, 수익률 테마를 붙인다.

실행: DATA=~/.qbot/research-data <연구용 파이썬> sample.py
결과: ../sample.csv (code, name, industry, return_theme, rcept_no)
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd

DATA = pathlib.Path(os.environ["DATA"])
OUT = pathlib.Path(__file__).resolve().parent.parent
SEED, N = 20260923, 100

fs = pd.read_csv(DATA / "bt/fs_ext.csv", dtype={"code": str}, low_memory=False)
last = fs.sort_values("fy_end").groupby("code").tail(1)
last = last[last.industry_latest.notna()]

# 가장 최근 사업보고서 접수번호(봇 DB, 읽기 전용)
c = sqlite3.connect(f"file:{pathlib.Path.home()}/.qbot/qbot-paper.sqlite3?mode=ro", uri=True)
ann = pd.read_sql(
    "select i.code, f.rcept_no, f.rcept_date from filing f join instrument i on i.id=f.instrument_id "
    "where f.report_kind='annual' order by f.rcept_date",
    c,
).groupby("code").tail(1)
c.close()
last = last.merge(ann, on="code")

# 수익률 테마: 07 themes.py와 같은 방식(120일 잔차 상관, 평균 연결, 30개), 자료 마지막 월말 기준
sys.path.insert(0, str(OUT.parent / "07-formula-ensemble" / "scripts"))
import themes as TH  # noqa: E402

t = TH.cal[TH.cal <= TH.cal[-1]].to_series().resample("ME").last().dropna().iloc[-2]  # 마지막 완결 월말
codes = [x for x in last.code if x in TH.C.columns and TH.C[x].iloc[-20:].notna().all()]
lab, _ = TH.themes_at(t, codes)
last["return_theme"] = last.code.map(lab)
last = last[last.return_theme.notna()]

# 업종이 서로 다른 종목부터: 업종을 무작위로 섞어 업종마다 1개씩, 100개가 될 때까지
rng = np.random.default_rng(SEED)
inds = last.industry_latest.unique()
rng.shuffle(inds)
pick = []
for ind in inds:
    pool = last[last.industry_latest == ind]
    pick.append(pool.iloc[rng.integers(len(pool))])
    if len(pick) == N:
        break
S = pd.DataFrame(pick)[["code", "name", "industry_latest", "market", "return_theme", "rcept_no", "rcept_date"]]
S = S.rename(columns={"industry_latest": "industry"})
S["return_theme"] = S.return_theme.astype(int)
S.to_csv(OUT / "sample.csv", index=False)
print(len(S), "종목,", S.industry.nunique(), "업종,", S.return_theme.nunique(), "수익률 테마, 기준", t.date())

"""09 평가: Laya 변형 3개 vs 업종명 키워드 규칙 vs 최빈값·무작위. 사람 라벨이 정답.

실행: <연구용 파이썬> evaluate.py
결과: ../results.csv, ../calibration.csv, ../keyword_rule.csv
"""

from __future__ import annotations

import pathlib
import re

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score

HERE = pathlib.Path(__file__).resolve().parent.parent
H = pd.read_csv(HERE / "labels_human.csv", dtype={"code": str})
P = pd.read_csv(HERE / "laya_predictions.csv", dtype={"code": str})
LABELS = [c[2:] for c in P.columns if c.startswith("p_") and c != "p_top1"]

# 업종명(통계청 표준산업분류 이름) → 테마. 위에서부터 먼저 걸리는 것. Laya 결과를 보기 전에 고정
RULE = [
    ("반도체", r"반도체"),
    ("2차전지", r"전지|축전지"),
    ("바이오·제약", r"의약|의약품|생물학|제약"),
    ("의료기기", r"의료|의료용|정형외과|치과|진단"),
    ("자동차·부품", r"자동차|트레일러"),
    ("조선·해운", r"선박|조선|해상|수상 운송|항만|하역"),
    ("방산·항공", r"항공|무기|총포|우주"),
    ("건설·건자재", r"건설|건물|토목|공사업|시멘트|콘크리트|비금속 광물|유리|석재|목재|나무제품|내화"),
    ("화학·소재", r"화학|플라스틱|고무|펄프|종이|판지|비료|농약|섬유 제조업|화학섬유"),
    ("철강·금속", r"철강|1차 금속|비철|금속 주조|금속|주조"),
    ("인터넷·게임·콘텐츠", r"소프트웨어|컴퓨터 프로그래밍|정보|게임|영화|방송|음악|오디오물|출판|광고|포털|통신"),
    ("금융", r"은행|저축|금융|보험|신탁|투자|여신"),
    ("음식료", r"식품|식료품|음료|주류|담배|제분|도축|육류|수산|낙농|곡물|과자|농산물|축산|제과"),
    ("에너지·유틸리티", r"전기|전력|가스|석유|발전|증기"),
    ("유통·소비재", r"도매|소매|의복|의류|신발|가방|화장품|가구|가정용|섬유|피혁|귀금속|장신구"),
]


def rule(ind: str) -> str:
    for lab, pat in RULE:
        if re.search(pat, str(ind)):
            return lab
    return "기타"


def ece(conf, correct, bins=10):
    conf, correct = np.asarray(conf), np.asarray(correct, float)
    edges = np.linspace(0, 1, bins + 1)
    tot = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            tot += m.mean() * abs(conf[m].mean() - correct[m].mean())
    return tot


H["rule"] = H.industry.map(rule)
H[["code", "industry", "rule"]].to_csv(HERE / "keyword_rule.csv", index=False)
clear = H.ambiguous == 0
maj = H.human.value_counts().idxmax()
rng = np.random.default_rng(0)
rows = []


def add(name, pred, top3=None, conf=None):
    ok = pred.values == H.human.values
    r = {
        "방법": name,
        "정확도(100)": ok.mean(),
        "정확도(모호 제외 70)": ok[clear.values].mean(),
        "top3 정확도": np.nan if top3 is None else np.mean([h in t.split("|") for h, t in zip(H.human, top3, strict=True)]),
        "ECE": np.nan if conf is None else ece(conf, ok),
        "평균 확신": np.nan if conf is None else float(np.mean(conf)),
        "수익률 테마 ARI": adjusted_rand_score(H.return_theme, pred),
    }
    rows.append(r)
    return ok


add("최빈값(" + maj + ")", pd.Series([maj] * len(H)))
add("무작위(16개 균등, 1회)", pd.Series(rng.choice(LABELS, len(H))))
add("업종명 키워드 규칙", H.rule)
cal_rows = []
for v, g in P.groupby("variant", sort=True):
    g = g.set_index("code").loc[H.code]
    ok = add(f"Laya {v}", g.top1.reset_index(drop=True), g.top3.values, g.p_top1.values)
    for lo, hi in [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]:
        m = (g.p_top1.values > lo) & (g.p_top1.values <= hi)
        cal_rows.append({"변형": v, "확신 구간": f"{lo:.1f}~{hi:.1f}", "개수": int(m.sum()),
                         "평균 확신": g.p_top1.values[m].mean() if m.any() else np.nan,
                         "실제 정확도": ok[m].mean() if m.any() else np.nan})
rows.append({"방법": "사람 라벨 자체", "정확도(100)": 1.0, "정확도(모호 제외 70)": 1.0,
             "수익률 테마 ARI": adjusted_rand_score(H.return_theme, H.human)})
R = pd.DataFrame(rows)
R.to_csv(HERE / "results.csv", index=False, float_format="%.3f")
pd.DataFrame(cal_rows).to_csv(HERE / "calibration.csv", index=False, float_format="%.3f")
print(R.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
print(pd.DataFrame(cal_rows).to_string(index=False, float_format=lambda x: f"{x:.3f}"))

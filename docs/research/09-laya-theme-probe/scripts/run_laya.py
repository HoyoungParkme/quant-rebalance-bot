"""Laya zero-shot 테마 분류. 변형 3개를 미리 정해 두고 전부 돌린다(좋은 것만 고르지 않는다).

실행: HF_HOME=~/.qbot/research-data/laya/hf <laya 파이썬> run_laya.py
결과: ../laya_predictions.csv (code, variant, top1, p_top1, top3, 16개 확률)
"""

from __future__ import annotations

import csv
import json
import pathlib
import time

import laya

HERE = pathlib.Path(__file__).resolve().parent.parent
TEXTS = pathlib.Path.home() / ".qbot/research-data/laya/texts"
LABELS = [
    "반도체", "2차전지", "바이오·제약", "의료기기", "자동차·부품", "조선·해운", "방산·항공", "건설·건자재",
    "화학·소재", "철강·금속", "인터넷·게임·콘텐츠", "금융", "유통·소비재", "음식료", "에너지·유틸리티", "기타",
]
DESC = {
    "반도체": "반도체 칩·장비·소재·부품", "2차전지": "배터리·전지 소재·전지 장비", "바이오·제약": "의약품·바이오·건강기능식품",
    "의료기기": "의료기기·진단", "자동차·부품": "자동차·부품·차량 서비스", "조선·해운": "조선·선박 기자재·해운·항만",
    "방산·항공": "방산·항공기·항공 운송·우주", "건설·건자재": "건설·건축 자재·공사", "화학·소재": "화학·플라스틱·섬유 원료·제지",
    "철강·금속": "철강·비철·주물·금속 가공", "인터넷·게임·콘텐츠": "소프트웨어·IT 서비스·게임·방송·엔터",
    "금융": "은행·증권·보험·투자·결제", "유통·소비재": "유통·의류·화장품·가구·가전", "음식료": "식품·음료·주류·담배·농축수산",
    "에너지·유틸리티": "전력·가스·정유·신재생", "기타": "위에 해당하지 않음",
}
VARIANTS = {
    "V1_목록": {"type": "choice", "instructions": "이 회사의 주력 사업(매출이 가장 큰 사업)은 어느 테마에 속하는가?",
                "criteria": LABELS},
    "V2_설명": {"type": "choice", "instructions": "이 회사의 주력 사업(매출이 가장 큰 사업)은 어느 테마에 속하는가?",
                "criteria": DESC},
    "V3_영어지시": {"type": "choice",
                 "instructions": "Which industry theme does this company's main business (largest revenue) belong to?",
                 "criteria": LABELS},
}


def main() -> None:
    rows = list(csv.DictReader(open(HERE / "sample.csv")))
    t0 = time.time()
    ag = laya.load("convaiinnovations/laya", subfolder="multilingual", device="cpu")
    load_s = time.time() - t0
    router = laya.Router()
    out, lat = [], []
    for r in rows:
        text = (TEXTS / f"{r['code']}.txt").read_text()
        state = f"회사명: {r['name']}\n{text}"
        for name, q in VARIANTS.items():
            t = time.time()
            res = ag.system_one(state, {"theme": q})
            lat.append(time.time() - t)
            a = res["answers"]["theme"]
            probs = a["probabilities"]
            if name == "V2_설명":  # 설명 딕셔너리여도 키가 라벨이다
                probs = {k: probs[k] for k in LABELS}
            top3 = sorted(probs, key=probs.get, reverse=True)[:3]
            out.append({"code": r["code"], "variant": name, "top1": a["choice"], "p_top1": probs[a["choice"]],
                        "confidence": a["confidence"], "top3": "|".join(top3),
                        **{f"p_{k}": probs[k] for k in LABELS}})
    route = router.route(f"회사명: {rows[0]['name']}\n" + (TEXTS / f"{rows[0]['code']}.txt").read_text())
    with open(HERE / "laya_predictions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    lat.sort()
    meta = {"model": "convaiinnovations/laya (multilingual subfolder, mmBERT-base)", "laya_version": laya.__version__,
            "router_choice_for_korean": route["model"], "router_reason": route["reason"],
            "load_seconds": round(load_s, 1), "latency_ms_median": round(1000 * lat[len(lat) // 2]),
            "latency_ms_p90": round(1000 * lat[int(len(lat) * 0.9)]), "n_calls": len(lat)}
    (HERE / "laya_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(meta)


if __name__ == "__main__":
    main()

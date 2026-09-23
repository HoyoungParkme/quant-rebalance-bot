"""검증·시험 구간 요약, 혼동행렬, 확신 구간별 정확도. 시험 구간은 여기서 한 번만 본다.

실행: <연구용 파이썬> evaluate.py
결과: ../results.csv, ../confusion.csv, ../calibration.csv
"""

from __future__ import annotations

import collections
import csv
import pathlib

import numpy as np

from train_eval import LABELS, ece, summarize

HERE = pathlib.Path(__file__).resolve().parent.parent
METHODS = ["baseline", "zeroshot", "head", "partial"]


def main() -> None:
    rows = [{**r, "p_max": float(r["p_max"])} for r in csv.DictReader((HERE / "predictions.csv").open())]
    res = [s for t in ("t1", "t2") for sp in ("val", "test") for m in METHODS if (s := summarize(rows, t, m, sp))]
    with (HERE / "results.csv").open("w", newline="") as f:
        keys = ["task", "method", "split", "n", "acc", "macro_f1", "ece", "cov_ge_0.9", "acc_ge_0.9"]
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(res)
    conf = []
    for t in ("t1", "t2"):
        for m in METHODS:
            c = collections.Counter((r["true"], r["pred"]) for r in rows
                                    if r["task"] == t and r["method"] == m and r["split"] == "test")
            conf += [{"task": t, "method": m, "true": a, "pred": b, "n": c[(a, b)]}
                     for a in LABELS[t] for b in LABELS[t] if c[(a, b)]]
    with (HERE / "confusion.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task", "method", "true", "pred", "n"])
        w.writeheader()
        w.writerows(conf)
    cal = []
    for t in ("t1", "t2"):
        for m in METHODS[1:]:
            rs = [r for r in rows if r["task"] == t and r["method"] == m and r["split"] == "test"]
            for lo, hi in [(0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.99), (0.99, 1.01)]:
                b = [r for r in rs if lo <= r["p_max"] < hi]
                if b:
                    cal.append({"task": t, "method": m, "conf_bin": f"{lo}-{min(hi, 1)}", "n": len(b),
                                "mean_conf": round(float(np.mean([r["p_max"] for r in b])), 3),
                                "acc": round(float(np.mean([r["true"] == r["pred"] for r in b])), 3)})
    with (HERE / "calibration.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task", "method", "conf_bin", "n", "mean_conf", "acc"])
        w.writeheader()
        w.writerows(cal)
    for s in res:
        print(s)
    _ = ece


if __name__ == "__main__":
    main()

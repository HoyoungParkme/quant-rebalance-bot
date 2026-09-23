"""Laya 공시 분류: zero-shot, 머리만 학습, 윗부분 6층 미세조정. 키워드 기준선과 비교.

실행: HF_HOME=~/.qbot/research-data/laya/hf <laya 파이썬> train_eval.py [zeroshot|head|partial|baseline|all]
입력: ~/.qbot/research-data/laya/disclosure/dataset.jsonl (prep.py)
결과: ../predictions.csv (rcept_no, 과제, 방법, 분할, 정답, 예측, 확률 — 본문 없음), ../runs.json(시간·설정)
      체크포인트는 ~/.qbot/research-data/laya/disclosure/ckpt/

사전 고정(README 0절): 입력 512토큰, 학습 ≤2023, 검증 2024(에폭 선택·온도 보정), 시험 2025~.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import pathlib
import random
import re
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

import laya
from laya.common import build_sequence, collate_items

HOME = pathlib.Path.home()
BASE = HOME / ".qbot/research-data/laya/disclosure"
CK = BASE / "ckpt"
CK.mkdir(exist_ok=True)
HERE = pathlib.Path(__file__).resolve().parent.parent
MAX_LEN, HEAD_LEN = 512, 256
SEED = 20260923

T1_LABELS = ["유상증자", "전환사채", "신주인수권부사채", "무상증자", "자기주식취득", "자기주식처분", "기타"]
T2_LABELS = ["소각", "주가안정·주주가치", "임직원 보상", "기타"]
Q = {
    "t1": {"type": "choice", "instructions": "이 공시(주요사항보고서)는 어떤 결정을 알리는가?", "criteria": T1_LABELS},
    "t2": {"type": "choice", "instructions": "이 자기주식 취득의 목적은 무엇인가?", "criteria": T2_LABELS},
}
LABELS = {"t1": T1_LABELS, "t2": T2_LABELS}


# ---------------- 시간 규칙 ----------------
def threads_now() -> int:
    hm = dt.datetime.now().hour * 100 + dt.datetime.now().minute
    return 4 if 1820 <= hm < 1940 else 12  # 봇 저녁 작업 시간엔 CPU를 비운다


def set_threads() -> None:
    n = threads_now()
    if torch.get_num_threads() != n:
        torch.set_num_threads(n)


# ---------------- 자료 ----------------
def load_data():
    rows = [json.loads(line) for line in (BASE / "dataset.jsonl").open()]
    items = []  # (task, rcept_no, split, label_idx, text)
    for r in rows:
        items.append(("t1", r["rcept_no"], r["split"], T1_LABELS.index(r["t1"]), r["t1_text"]))
        if r.get("t2"):
            items.append(("t2", r["rcept_no"], r["split"], T2_LABELS.index(r["t2"]), r["t2_text"]))
    return items


def encode(ag, items):
    out = []
    for task, no, split, y, text in items:
        q = ag._to_internal(Q[task])
        ids, mk = build_sequence(ag.tok, text, q, MAX_LEN, HEAD_LEN)
        out.append({"ids": ids, "markers": mk, "qtype": 0, "label": y, "task": task, "no": no, "split": split})
    return out


def batches(enc, bs, shuffle=False, seed=0):
    idx = list(range(len(enc)))
    if shuffle:
        random.Random(seed).shuffle(idx)
    for i in range(0, len(idx), bs):
        yield [enc[j] for j in idx[i : i + bs]]


# ---------------- 모델 ----------------
def head_forward(model, h, att, mpos, mmask, qtype):
    """DecisionModel.forward의 인코더 뒤 부분. 캐시한 인코더 출력으로 머리만 학습할 때 쓴다."""
    h = h + model.type_emb(qtype)[:, None, :]
    pad = ~att.bool()
    for layer in model.head.layers:
        h = layer(h, src_key_padding_mask=pad)
    idx = mpos.clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
    m = torch.gather(h, 1, idx)
    return model.scorer(m).squeeze(-1).float().masked_fill(~mmask, -1e4)


def logits_of(model, group, pad_id):
    b = collate_items([group], pad_id)
    lg, _ = model(b["input_ids"], b["attention_mask"], b["marker_pos"], b["marker_mask"], b["qtype"])
    return lg, b["label"]


@torch.no_grad()
def predict(model, enc, pad_id, bs=8):
    model.eval()
    res = []
    for g in batches(enc, bs):
        set_threads()
        lg, _ = logits_of(model, g, pad_id)
        for it, row in zip(g, lg):
            k = len(it["markers"])
            res.append(row[:k].numpy())
    return res


# ---------------- 측정 ----------------
def softmax(z, t=1.0):
    z = np.asarray(z, dtype=np.float64) / t
    z = np.exp(z - z.max())
    return z / z.sum()


def fit_temperature(logits, ys):
    """검증 구간 로그 손실을 최소로 하는 온도 하나. 0.25~8 격자."""
    best, bt = 1e18, 1.0
    for t in np.exp(np.linspace(np.log(0.25), np.log(8), 60)):
        nll = -np.mean([np.log(softmax(z, t)[y] + 1e-12) for z, y in zip(logits, ys)])
        if nll < best:
            best, bt = nll, float(t)
    return bt


def macro_f1(y, p, k):
    f1s = []
    for c in range(k):
        tp = sum(1 for a, b in zip(y, p) if a == c and b == c)
        fp = sum(1 for a, b in zip(y, p) if a != c and b == c)
        fn = sum(1 for a, b in zip(y, p) if a == c and b != c)
        if tp + fp + fn == 0:
            continue  # 그 구간에 없는 반은 평균에서 뺀다
        f1s.append(0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f1s)) if f1s else float("nan")


def ece(conf, correct, bins=15):
    conf, correct = np.asarray(conf), np.asarray(correct, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(conf[m].mean() - correct[m].mean())
    return float(e)


def summarize(rows, task, method, split):
    rs = [r for r in rows if r["task"] == task and r["method"] == method and r["split"] == split]
    if not rs:
        return None
    k = len(LABELS[task])
    y = [LABELS[task].index(r["true"]) for r in rs]
    p = [LABELS[task].index(r["pred"]) for r in rs]
    conf = [r["p_max"] for r in rs]
    corr = [a == b for a, b in zip(y, p)]
    hi = [c for c, cf in zip(corr, conf) if cf >= 0.9]
    out = {"task": task, "method": method, "split": split, "n": len(rs), "acc": round(float(np.mean(corr)), 4),
           "macro_f1": round(macro_f1(y, p, k), 4)}
    if method != "baseline":
        out |= {"ece": round(ece(conf, corr), 4), "cov_ge_0.9": round(len(hi) / len(rs), 4),
                "acc_ge_0.9": round(float(np.mean(hi)), 4) if hi else None}
    return out


# ---------------- 키워드 기준선 (README 0절) ----------------
def baseline_t1(text: str) -> str:
    s = re.sub(r"\s+", "", text)
    if "신주인수권에관한사항" in s or ("신주인수권" in s and "사채의종류" in s):
        return "신주인수권부사채"
    if "전환에관한사항" in s or "전환가액" in s:
        return "전환사채"
    if "처분예정주식" in s or "처분목적" in s:
        return "자기주식처분"
    if "취득예정주식" in s or "취득목적" in s:
        return "자기주식취득"
    if "1주당신주배정주식수" in s and "증자방식" not in s:
        return "무상증자"
    if "증자방식" in s or "신주발행가액" in s or ("신주의종류와수" in s and "자금조달의목적" in s):
        return "유상증자"
    return "기타"


def baseline_t2(text: str) -> str:
    p = re.sub(r"\s+", "", text)
    if "소각" in p:
        return "소각"
    if re.search(r"임직원|성과보상|상여|우리사주|주식매수선택권|스톡옵션", p):
        return "임직원 보상"
    if re.search(r"주가안정|주주가치|주주환원|가치제고|주주이익|주가의안정", p):
        return "주가안정·주주가치"
    return "기타"


# ---------------- 실행 ----------------
def rows_from(enc, logits, method, t_by_task=None):
    out = []
    for it, z in zip(enc, logits):
        t = (t_by_task or {}).get(it["task"], 1.0)
        pr = softmax(z, t)
        j = int(pr.argmax())
        out.append({"rcept_no": it["no"], "task": it["task"], "method": method, "split": it["split"],
                    "true": LABELS[it["task"]][it["label"]], "pred": LABELS[it["task"]][j],
                    "p_max": round(float(pr[j]), 4), "probs": "|".join(f"{v:.4f}" for v in pr)})
    return out


def fitted_temps(enc, logits):
    temps = {}
    for task in ("t1", "t2"):
        zs = [z for it, z in zip(enc, logits) if it["task"] == task and it["split"] == "val"]
        ys = [it["label"] for it in enc if it["task"] == task and it["split"] == "val"]
        temps[task] = fit_temperature(zs, ys) if zs else 1.0
    return temps


def val_loss(enc, logits):
    zs = [(z, it["label"]) for it, z in zip(enc, logits) if it["split"] == "val"]
    return float(-np.mean([np.log(softmax(z)[y] + 1e-12) for z, y in zs]))


def run_zeroshot(ag, enc, pad):
    ev = [e for e in enc if e["split"] in ("val", "test")]
    t = time.time()
    lg = predict(ag.model, ev, pad)
    # zero-shot은 체크포인트의 온도(1.0)를 그대로 쓴다 — 비교용
    return rows_from(ev, lg, "zeroshot"), {"seconds": round(time.time() - t), "n": len(ev),
                                           "ms_per_item": round(1000 * (time.time() - t) / max(1, len(ev)))}


def run_head(ag, enc, pad, epochs=6):
    """인코더를 고정하고 출력을 한 번만 계산해 둔 뒤, 판단 머리만 학습한다."""
    model = ag.model
    model.eval()
    t0 = time.time()
    cache = []
    with torch.no_grad():
        for g in batches(enc, 8):
            set_threads()
            b = collate_items([g], pad)
            h = model.encoder(input_ids=b["input_ids"], attention_mask=b["attention_mask"]).last_hidden_state
            for i, it in enumerate(g):
                L = len(it["ids"])
                cache.append(h[i, :L].to(torch.float16).clone())
    enc_s = time.time() - t0
    head_params = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    init = {n: p.detach().clone() for n, p in model.named_parameters() if not n.startswith("encoder.")}
    opt = torch.optim.AdamW(head_params, lr=1e-4, weight_decay=0.01)
    tr = [i for i, e in enumerate(enc) if e["split"] == "train"]

    def fwd(ix):
        g = [enc[i] for i in ix]
        b = collate_items([g], pad)
        L = b["input_ids"].size(1)
        h = torch.zeros(len(ix), L, cache[0].size(-1))
        for r, i in enumerate(ix):
            h[r, : cache[i].size(0)] = cache[i].float()
        return head_forward(model, h, b["attention_mask"], b["marker_pos"], b["marker_mask"], b["qtype"]), b["label"]

    best, best_state, hist = 1e18, None, []
    for ep in range(epochs):
        model.train()
        random.Random(SEED + ep).shuffle(tr)
        for i in range(0, len(tr), 16):
            set_threads()
            lg, y = fwd(tr[i : i + 16])
            loss = F.cross_entropy(lg, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            lg_all = []
            for i in range(0, len(enc), 32):
                lg, _ = fwd(list(range(i, min(i + 32, len(enc)))))
                lg_all += [row[: len(enc[i + r]["markers"])].numpy() for r, row in enumerate(lg)]
        vl = val_loss(enc, lg_all)
        hist.append(round(vl, 4))
        if vl < best:
            best, best_state, best_logits = vl, {n: p.detach().clone() for n, p in model.named_parameters()
                                                  if not n.startswith("encoder.")}, lg_all
    temps = fitted_temps(enc, best_logits)
    rows = rows_from(enc, best_logits, "head", temps)
    with torch.no_grad():  # 다음 방법을 위해 머리를 원래대로
        for n, p in model.named_parameters():
            if n in init:
                p.copy_(init[n])
    torch.save(best_state, CK / "head.pt")
    return rows, {"encode_seconds": round(enc_s), "total_seconds": round(time.time() - t0), "val_loss_by_epoch": hist,
                  "temperature": temps}


def run_partial(ag, enc, pad, epochs=2, top=6):
    model = ag.model
    for n, p in model.named_parameters():
        p.requires_grad = not n.startswith("encoder.")
    for layer in model.encoder.layers[-top:]:
        for p in layer.parameters():
            p.requires_grad = True
    for p in model.encoder.final_norm.parameters():
        p.requires_grad = True
    enc_p = [p for n, p in model.named_parameters() if p.requires_grad and n.startswith("encoder.")]
    head_p = [p for n, p in model.named_parameters() if p.requires_grad and not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc_p, "lr": 2e-5}, {"params": head_p, "lr": 1e-4}], weight_decay=0.01)
    tr = [e for e in enc if e["split"] == "train"]
    ev = enc
    t0 = time.time()
    best, hist, best_logits = 1e18, [], None
    steps = math.ceil(len(tr) / 8) * epochs
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 50) * max(0.0, 1 - s / steps))
    for ep in range(epochs):
        model.train()
        for k, g in enumerate(batches(tr, 8, shuffle=True, seed=SEED + ep)):
            set_threads()
            lg, y = logits_of(model, g, pad)
            loss = F.cross_entropy(lg, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_([*enc_p, *head_p], 1.0)
            opt.step()
            sched.step()
            if k % 25 == 0:
                print(f"partial ep{ep} step{k} loss {loss.item():.3f} {round(time.time() - t0)}s", flush=True)
        lg_all = predict(model, ev, pad)
        vl = val_loss(ev, lg_all)
        hist.append(round(vl, 4))
        print(f"partial ep{ep} val_loss {vl:.4f}", flush=True)
        if vl < best:
            best, best_logits = vl, lg_all
            torch.save({n: p for n, p in model.state_dict().items()}, CK / "partial.pt")
    temps = fitted_temps(ev, best_logits)
    return rows_from(ev, best_logits, "partial", temps), {"total_seconds": round(time.time() - t0),
                                                          "val_loss_by_epoch": hist, "temperature": temps,
                                                          "top_layers": top, "epochs": epochs}


def run_baseline(items):
    rows = []
    for task, no, split, y, text in items:
        pred = baseline_t1(text) if task == "t1" else baseline_t2(text)
        rows.append({"rcept_no": no, "task": task, "method": "baseline", "split": split,
                     "true": LABELS[task][y], "pred": pred, "p_max": 1.0, "probs": ""})
    return rows


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    torch.manual_seed(SEED)
    set_threads()
    items = load_data()
    ag = laya.load("convaiinnovations/laya", subfolder="multilingual", device="cpu")
    pad = ag.tok.pad_token_id
    enc = encode(ag, items)
    pred_path, runs_path = HERE / "predictions.csv", HERE / "runs.json"
    old = list(csv.DictReader(pred_path.open())) if pred_path.exists() else []
    runs = json.loads(runs_path.read_text()) if runs_path.exists() else {}
    todo = ["baseline", "zeroshot", "head", "partial"] if what == "all" else [what]
    for m in todo:
        print("==", m, dt.datetime.now().isoformat(timespec="seconds"), flush=True)
        if m == "baseline":
            rows, meta = run_baseline(items), {}
        elif m == "zeroshot":
            rows, meta = run_zeroshot(ag, enc, pad)
        elif m == "head":
            rows, meta = run_head(ag, enc, pad)
        else:
            rows, meta = run_partial(ag, enc, pad)
        old = [r for r in old if r["method"] != m] + rows
        runs[m] = meta | {"finished": dt.datetime.now().isoformat(timespec="seconds")}
        with pred_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["rcept_no", "task", "method", "split", "true", "pred", "p_max", "probs"])
            w.writeheader()
            w.writerows(old)
        runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=1))
    # 검증 구간 요약만 찍는다. 시험 구간은 evaluate.py에서 마지막에 한 번 본다
    rows = [{**r, "p_max": float(r["p_max"])} for r in old]
    for task in ("t1", "t2"):
        for m in ("baseline", "zeroshot", "head", "partial"):
            s = summarize(rows, task, m, "val")
            if s:
                print(s)


if __name__ == "__main__":
    main()

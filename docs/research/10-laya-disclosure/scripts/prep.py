"""원문 zip → 정리한 본문, 정답, 누수 차단. 원문·본문은 저장소 밖, 저장소에는 접수번호·라벨만.

실행: <laya 파이썬> prep.py
결과: ~/.qbot/research-data/laya/disclosure/dataset.jsonl (본문 포함, 저장소 밖)
      ../dataset_labels.csv (rcept_no, 접수일, 분할, T1 정답, T2 정답 — 저장소)
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import re
import zipfile

HOME = pathlib.Path.home()
BASE = HOME / ".qbot/research-data/laya/disclosure"
HERE = pathlib.Path(__file__).resolve().parent.parent
MAX_CHARS = 2500  # 토큰 512개에 넉넉히. 모델 입력에서 다시 자른다

# T1 누수 차단: 결정 이름. 띄어쓰기가 들쭉날쭉해 글자 사이 공백을 허용한다
DECISION_NAMES = [
    "유상증자결정", "무상증자결정", "유무상증자결정", "전환사채권발행결정", "신주인수권부사채권발행결정",
    "자기주식취득결정", "자기주식처분결정", "주요사항보고서", "교환사채권발행결정",
]


def loose(word: str) -> str:
    return r"\s*".join(map(re.escape, word))


LEAK_RE = re.compile("|".join(loose(w) for w in DECISION_NAMES))


def xml_text(zbytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    name = max(z.namelist(), key=lambda n: z.getinfo(n).file_size)
    raw = z.read(name)
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            s = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        s = raw.decode("utf-8", "ignore")
    s = re.sub(r"<(TD|TH|TE|TU)[^>]*>", " | ", s, flags=re.I)  # 표 칸 경계를 남긴다
    s = re.sub(r"<(TR|P|BR|TITLE)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&[a-z#0-9]+;", " ", s)
    s = re.sub(r"[ \t　]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def drop_cover(s: str) -> str:
    """표지(금융위원회 귀중, 회사명, 대표이사, 주소, 전화…)를 뗀다. 첫 번호 항목("1.")부터 쓴다."""
    m = re.search(r"(?m)^\s*\|?\s*1\s*\.\s*[가-힣]", s)  # "1.2" 같은 서식 버전 줄은 건너뛴다
    return s[m.start():] if m else s


PURPOSE_RE = re.compile(r"취\s*득\s*목\s*적\s*\|([^|\n]{2,200})")


def t2_label(purpose: str) -> str:
    p = re.sub(r"\s+", "", purpose)
    if "소각" in p:
        return "소각"
    if re.search(r"임직원|성과보상|상여|우리사주|주식매수선택권|스톡옵션|보상", p):
        return "임직원 보상"
    if re.search(r"주가안정|주주가치|주주환원|가치제고|주주이익|주가의안정", p):
        return "주가안정·주주가치"
    return "기타"


def split_of(d: str) -> str:
    return "train" if d <= "20231231" else ("val" if d <= "20241231" else "test")


def main() -> None:
    sample = json.loads((BASE / "sample.json").read_text())
    out, labels, skipped = [], [], {"no_doc": 0, "empty": 0, "no_purpose": 0}
    for x in sample:
        zp = BASE / "docs" / f"{x['rcept_no']}.zip"
        if not zp.exists():
            skipped["no_doc"] += 1
            continue
        try:
            s = drop_cover(xml_text(zp.read_bytes()))
        except (zipfile.BadZipFile, KeyError):
            skipped["empty"] += 1
            continue
        t2 = None
        if x["kind"] == "자기주식취득":
            m = PURPOSE_RE.search(s)
            if not m:
                skipped["no_purpose"] += 1
            else:
                t2 = t2_label(m.group(1))
                s_t2 = s[: m.start()] + "취득목적 | [가림]" + s[m.end():]  # 목적 칸만 가린다
        s_t1 = LEAK_RE.sub(" ", s)
        rec = {"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "split": split_of(x["rcept_dt"]),
               "t1": x["kind"], "t1_text": s_t1[:MAX_CHARS]}
        if t2:
            rec |= {"t2": t2, "t2_text": LEAK_RE.sub(" ", s_t2)[:MAX_CHARS]}
        if len(s_t1) < 50:
            skipped["empty"] += 1
            continue
        out.append(rec)
        labels.append({"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "split": rec["split"],
                       "t1": x["kind"], "t2": t2 or ""})
    with (BASE / "dataset.jsonl").open("w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (HERE / "dataset_labels.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(labels[0]))
        w.writeheader()
        w.writerows(labels)
    print("표본", len(sample), "사용", len(out), "제외", skipped)


if __name__ == "__main__":
    main()

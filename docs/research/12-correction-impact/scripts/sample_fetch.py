"""정정 영향 측정 1단계: 층화 표본을 뽑고 원본 문서(document.xml)를 받는다. 원문은 저장소 밖에만 둔다.

실행: ~/.qbot/research-data/.venv/bin/python sample_fetch.py sample | fetch
결과: ../sample.csv, ~/.qbot/research-data/correction-impact/docs/<rcept_no>.zip

전자공시 규칙: 키는 ~/.qbot/.env의 DART_API_KEY(값 출력·기록 금지), 요청 간 3.5초 이상, 총 120건 이하,
한국시간 20:00~21:30에는 부르지 않는다(봇 저녁 수집), 연결 문제·HTTP 오류·한도(020)면 즉시 멈춘다.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import random
import sqlite3
import sys
import time

import httpx

HOME = pathlib.Path.home()
OUT = pathlib.Path(__file__).resolve().parent.parent
DOCS = HOME / ".qbot/research-data/correction-impact/docs"
DB = f"file:{HOME}/.qbot/qbot-paper.sqlite3?mode=ro"
SEED, CAP, GAP = 20260928, 120, 3.6
BANDS = [(-10_000, -1, "neg"), (0, 30, "0-30"), (31, 90, "31-90"), (91, 365, "91-365"), (366, 10_000, "366+")]


def band(days: int) -> str:
    return next(b for lo, hi, b in BANDS if lo <= days <= hi)


def sample() -> None:
    c = sqlite3.connect(DB, uri=True)
    rows = c.execute(
        """select f.id, f.rcept_no, f.rcept_date, f.report_kind, f.title, f.numbers_rcept_no, g.rcept_date
           from filing f left join filing g on g.rcept_no = f.numbers_rcept_no
           where f.numbers_rcept_no is not null"""
    ).fetchall()
    cand = []
    for fid, rno, rdate, kind, title, nno, ndate in rows:
        # 정정본 접수일: 저장돼 있으면 그 날, 없으면 접수번호 앞 8자리
        nd = ndate or f"{nno[:4]}-{nno[4:6]}-{nno[6:8]}"
        days = (dt.date.fromisoformat(nd) - dt.date.fromisoformat(rdate)).days
        cand.append(dict(filing_id=fid, rcept_no=rno, rcept_date=rdate, kind=kind, title=title,
                         numbers_rcept_no=nno, numbers_date=nd, gap_days=days, gap_band=band(days),
                         year=rdate[:4]))
    rng = random.Random(SEED)
    strata: dict[tuple, list] = {}
    for r in cand:
        strata.setdefault((r["gap_band"], r["kind"] if r["kind"] in ("annual", "correction") else "interim"), []).append(r)
    # 간격 구간 4개 × 종류 3개 = 12층. 층마다 최대 7건, 작은 층은 전부
    picked = []
    for key in sorted(strata):
        pool = sorted(strata[key], key=lambda r: r["rcept_no"])
        rng.shuffle(pool)
        picked += pool[:7]
    picked.sort(key=lambda r: r["rcept_no"])
    with open(OUT / "sample.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(picked[0]))
        w.writeheader()
        w.writerows(picked)
    print(len(cand), "후보 →", len(picked), "표본")
    for key in sorted(strata):
        print(" ", key, len(strata[key]), "→", sum(1 for p in picked if (p["gap_band"], p["kind"] if p["kind"] in ("annual", "correction") else "interim") == key))


class Stop(Exception):
    pass


def fetch() -> None:
    key = next(
        line.split("=", 1)[1].strip().strip("\"'")
        for line in (HOME / ".qbot/.env").read_text().splitlines()
        if line.startswith("DART_API_KEY=")
    )
    todo = [r["rcept_no"] for r in csv.DictReader(open(OUT / "sample.csv"))]
    todo = [r for r in todo if not (DOCS / f"{r}.zip").exists()]
    n, last = 0, 0.0
    with httpx.Client(timeout=60) as client:
        for rno in todo:
            hm = dt.datetime.now().hour * 100 + dt.datetime.now().minute
            if 2000 <= hm < 2130:
                raise Stop("봇 저녁 수집 시간이라 멈춘다")
            if n >= CAP:
                raise Stop("호출 상한")
            wait = GAP - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = client.get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key": key, "rcept_no": rno})
            except httpx.HTTPError as e:
                raise Stop(f"연결 문제 {type(e).__name__}") from e
            finally:
                last = time.time()
                n += 1
            if r.status_code != 200:
                raise Stop(f"HTTP {r.status_code}")
            if not r.content.startswith(b"PK"):
                txt = r.text[:200]
                if "020" in txt:
                    raise Stop("한도 초과(020)")
                print(rno, "zip 아님:", txt.replace(key, "***")[:120])
                continue
            (DOCS / f"{rno}.zip").write_bytes(r.content)
    print("호출", n, "건")


if __name__ == "__main__":
    {"sample": sample, "fetch": fetch}[sys.argv[1]]()

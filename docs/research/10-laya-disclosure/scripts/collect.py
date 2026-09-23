"""주요사항보고서 목록과 원문을 받는다. 원문은 저장소 밖에만 둔다.

실행: <laya 파이썬> collect.py list   # 2019-01 ~ 2026-09 목록 (월별, 이어받기)
      <laya 파이썬> collect.py docs   # 표본 원문 (이어받기)
결과: ~/.qbot/research-data/laya/disclosure/{list/*.json, docs/<rcept_no>.zip, calls.log}

전자공시 규칙(엄수):
- 키는 ~/.qbot/.env의 DART_API_KEY. 값을 출력·기록하지 않는다
- 요청 사이 3.5초 이상, 하루 3,000건 이하(봇과 2만 건 한도 공유)
- 한국시간 18:00~19:45에는 부르지 않는다(봇 저녁 수집)
- 연결 리셋·한도(020)·이상 응답이면 즉시 멈춘다
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import random
import sys
import time

import httpx

HOME = pathlib.Path.home()
BASE = HOME / ".qbot/research-data/laya/disclosure"
(BASE / "list").mkdir(parents=True, exist_ok=True)
(BASE / "docs").mkdir(parents=True, exist_ok=True)
LOG = BASE / "calls.log"
KEY = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in (HOME / ".qbot/.env").read_text().splitlines()
    if line.startswith("DART_API_KEY=")
)
GAP, DAY_CAP = 3.6, 3000
_last = [0.0]

# 사전 고정: 과제 T1의 반(class)과 표본 목표. T2는 자기주식취득결정 전부
T1_QUOTA = {"유상증자": 260, "전환사채": 260, "신주인수권부사채": 200, "무상증자": 200, "자기주식처분": 260, "기타": 320}


class Stop(Exception):
    pass


def calls_today() -> int:
    today = dt.date.today().isoformat()
    if not LOG.exists():
        return 0
    return sum(1 for line in LOG.read_text().splitlines() if line.startswith(today))


def guard() -> None:
    now = dt.datetime.now()
    hm = now.hour * 100 + now.minute
    if 1800 <= hm < 1945:
        raise Stop("봇 저녁 수집 시간(18:00~19:45)이라 멈춘다")
    if calls_today() >= DAY_CAP:
        raise Stop(f"오늘 {DAY_CAP}건을 다 썼다")
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)


def call(client: httpx.Client, path: str, params: dict) -> httpx.Response:
    guard()
    try:
        r = client.get(f"https://opendart.fss.or.kr/api/{path}", params={"crtfc_key": KEY, **params})
    except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
        raise Stop(f"연결 문제: {type(e).__name__}") from e
    finally:
        _last[0] = time.time()
        with LOG.open("a") as f:  # 날짜·경로만 남긴다(키 없음)
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {path}\n")
    if r.status_code != 200:
        raise Stop(f"HTTP {r.status_code}")
    return r


def months():
    d = dt.date(2019, 1, 1)
    while d <= dt.date(2026, 9, 1):
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, min(nxt - dt.timedelta(days=1), dt.date(2026, 9, 23))
        d = nxt


def do_list(client: httpx.Client) -> None:
    for a, b in months():
        out = BASE / "list" / f"{a:%Y-%m}.json"
        if out.exists():
            continue
        rows, page = [], 1
        while True:
            r = call(client, "list.json", {"bgn_de": f"{a:%Y%m%d}", "end_de": f"{b:%Y%m%d}", "pblntf_ty": "B",
                                           "page_no": page, "page_count": 100})
            j = r.json()
            if j.get("status") == "013":  # 조회 결과 없음
                break
            if j.get("status") != "000":
                raise Stop(f"list 상태 {j.get('status')} {j.get('message')}")
            rows += j["list"]
            if page >= int(j["total_page"]):
                break
            page += 1
        out.write_text(json.dumps(rows, ensure_ascii=False))
        print(a, len(rows), "누적 호출", calls_today(), flush=True)


def kind_of(report_nm: str) -> str | None:
    """보고서명 → 정답 반. 정정은 뺀다(같은 사건의 중복)."""
    if "[기재정정]" in report_nm or "정정" in report_nm.split("(")[0]:
        return None
    body = report_nm.split("(", 1)[-1] if "(" in report_nm else report_nm
    if "유무상증자" in body:
        return None  # 두 반이 섞인다
    table = [
        ("자기주식취득결정", "자기주식취득"), ("자기주식처분결정", "자기주식처분"),
        ("신주인수권부사채권발행결정", "신주인수권부사채"), ("전환사채권발행결정", "전환사채"),
        ("무상증자결정", "무상증자"), ("유상증자결정", "유상증자"),
    ]
    for key, lab in table:
        if key in body:
            return lab
    return "기타"


def select() -> list[dict]:
    rows = []
    for p in sorted((BASE / "list").glob("*.json")):
        for x in json.loads(p.read_text()):
            k = kind_of(x["report_nm"])
            if k and x.get("stock_code"):  # 상장사만
                rows.append({**x, "kind": k})
    rng = random.Random(20260923)
    picked = [x for x in rows if x["kind"] == "자기주식취득"]  # T2: 전부
    for k, n in T1_QUOTA.items():
        pool = [x for x in rows if x["kind"] == k]
        # 연도가 고르게 가도록 연도별로 섞어서 앞에서부터
        rng.shuffle(pool)
        by_year: dict[str, list] = {}
        for x in pool:
            by_year.setdefault(x["rcept_dt"][:4], []).append(x)
        take = []
        while len(take) < n and any(by_year.values()):
            for y in sorted(by_year):
                if by_year[y] and len(take) < n:
                    take.append(by_year[y].pop())
        picked += take
    (BASE / "sample.json").write_text(json.dumps(picked, ensure_ascii=False))
    return picked


def do_docs(client: httpx.Client) -> None:
    picked = json.loads((BASE / "sample.json").read_text()) if (BASE / "sample.json").exists() else select()
    todo = [x for x in picked if not (BASE / "docs" / f"{x['rcept_no']}.zip").exists()
            and not (BASE / "docs" / f"{x['rcept_no']}.bad").exists()]
    # 자사주 취득(T2)을 먼저, 그다음 T1 반을 섞어서 받는다(중간에 멈춰도 반이 고르게)
    random.Random(1).shuffle(todo)
    todo.sort(key=lambda x: x["kind"] != "자기주식취득")
    print("받을 원문", len(todo), "오늘 호출", calls_today(), flush=True)
    for i, x in enumerate(todo):
        r = call(client, "document.xml", {"rcept_no": x["rcept_no"]})
        if r.content[:2] == b"PK":
            (BASE / "docs" / f"{x['rcept_no']}.zip").write_bytes(r.content)
        else:
            head = r.text[:300]
            if "020" in head:
                raise Stop("사용 한도(020)")
            (BASE / "docs" / f"{x['rcept_no']}.bad").write_text(head[:120])
        if i % 100 == 0:
            print(i, "누적 호출", calls_today(), flush=True)


def main() -> int:
    what = sys.argv[1]
    try:
        with httpx.Client(timeout=60) as client:
            if what == "list":
                do_list(client)
            elif what == "select":
                print(len(select()))
            else:
                do_docs(client)
    except Stop as e:
        print("멈춤:", e, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

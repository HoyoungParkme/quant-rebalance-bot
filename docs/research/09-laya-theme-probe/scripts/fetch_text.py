"""사업보고서 원문에서 "II. 사업의 내용" 앞부분을 뽑는다. 원문은 저장소 밖에만 둔다.

실행: <연구용 파이썬> fetch_text.py   (키는 ~/.qbot/.env의 DART_API_KEY. 값을 출력하지 않는다)
결과: ~/.qbot/research-data/laya/texts/<code>.txt, fetch_log.csv(코드·상태·글자 수만)
전자공시는 몰아 부르면 IP를 막는다. 요청 사이 3.5초, 연결 리셋이면 즉시 멈춘다.
"""

from __future__ import annotations

import csv
import io
import pathlib
import re
import sys
import time
import zipfile

import httpx

HOME = pathlib.Path.home()
OUT = HOME / ".qbot/research-data/laya/texts"
OUT.mkdir(parents=True, exist_ok=True)
RAW = HOME / ".qbot/research-data/laya/raw"
RAW.mkdir(parents=True, exist_ok=True)
HERE = pathlib.Path(__file__).resolve().parent.parent
KEY = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in (HOME / ".qbot/.env").read_text().splitlines()
    if line.startswith("DART_API_KEY=")
)
GAP, MAX_CHARS = 3.5, 1500


def section(xml: str) -> str:
    """II. 사업의 내용 본문의 "1. 사업의 개요"부터. 목차(점선·쪽번호)는 건너뛴다."""
    txt = re.sub(r"<[^>]+>", " ", xml)
    txt = re.sub(r"&[a-z#0-9]+;", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    for m in re.finditer(r"1\.\s*(?:\([^)]{1,15}\))?\s*사업의\s*개요", txt):
        after = txt[m.end() : m.end() + 300]
        if "---" in after or "..." in after or re.search(r"2\.\s*주요\s*제품", after[:120]):
            continue  # 목차다
        if re.search(r"참조|참고", after[:40]):
            continue  # "I. 회사의 개요"의 "사업의 개요는 ...을 참조" 같은 안내 문구다
        body = txt[m.end() :]
        end = re.search(r"III\.\s*재무에\s*관한\s*사항", body)
        body = body[: end.start()] if end else body
        return body.strip()[:MAX_CHARS]
    return ""


def main() -> int:
    rows = list(csv.DictReader(open(HERE / "sample.csv")))
    log = []
    with httpx.Client(timeout=60) as c:
        for i, r in enumerate(rows):
            path = OUT / f"{r['code']}.txt"
            if path.exists() and path.stat().st_size > 1000:
                log.append((r["code"], "cached", path.stat().st_size))
                continue
            try:
                resp = c.get(
                    "https://opendart.fss.or.kr/api/document.xml",
                    params={"crtfc_key": KEY, "rcept_no": r["rcept_no"]},
                )
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                print(f"연결 문제로 멈춤({i}건째): {type(e).__name__}")
                break
            status, text = "ok", ""
            if resp.content[:2] != b"PK":
                status = "not_zip:" + re.sub(r"\s+", " ", resp.text[:80])
                if "020" in resp.text[:200]:  # 사용한도 초과
                    print("사용 한도 초과로 멈춤")
                    log.append((r["code"], status, 0))
                    break
            else:
                (RAW / f"{r['code']}.zip").write_bytes(resp.content)
                z = zipfile.ZipFile(io.BytesIO(resp.content))
                for n in sorted(z.namelist(), key=lambda n: z.getinfo(n).file_size, reverse=True):
                    raw = z.read(n)
                    for enc in ("utf-8", "euc-kr", "cp949"):
                        try:
                            xml = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    text = section(xml)
                    if text:
                        break
                status = "ok" if text else "no_section"
            if text:
                path.write_text(text)
            log.append((r["code"], status, len(text)))
            print(i, r["code"], status, len(text), flush=True)
            time.sleep(GAP)
    with open(HOME / ".qbot/research-data/laya/fetch_log.csv", "w", newline="") as f:
        csv.writer(f).writerows([("code", "status", "chars"), *log])
    return 0


if __name__ == "__main__":
    sys.exit(main())

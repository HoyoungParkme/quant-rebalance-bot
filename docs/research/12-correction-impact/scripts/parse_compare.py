"""정정 영향 측정 2단계: 원본 문서의 요약재무정보 표를 파싱해 봇 DB 숫자(정정본 숫자)와 비교한다.

실행: <python> parse_compare.py
결과: ../compare.csv(표본별 항목별 값·차이), ../summary.csv(항목·간격 구간별 요약)

파싱 규칙(억지로 맞추지 않는다 — 규칙에 안 맞으면 실패로 센다):
1. 목차 제목(TITLE)에 "요약재무정보"가 있는 절만 본다. 다음 목차 제목 전까지.
2. 그 절의 표마다, 앞 표가 끝난 뒤부터 이 표 시작까지의 글에서 "연결" 여부와 "단위"를 읽는다.
   봇 DB 행이 연결이면 "연결" 표, 별도면 "연결"이 없는 표. 단위(원·천원·백만원·억원)를 못 찾으면 실패.
3. 행 이름(한글만 남김)으로 항목을 고른다. 매출: 매출액·영업수익·수익(매출액), 영업이익: 영업이익·영업손익,
   당기순이익: 당기·분기·반기순이익(지배·비지배·귀속이 붙은 행 제외), 자본총계: 자본총계.
4. 값은 행 이름 다음의 첫 숫자 칸(당기). 괄호·△·- 부호는 음수, "-" 한 글자는 0.
비교 범위: 자본총계는 모든 보고서. 손익 항목은 사업보고서와 1분기보고서만(반기·3분기 요약표는 누적인데
봇 DB에는 분기 값만 있어 직접 비교할 수 없다).
"""

from __future__ import annotations

import csv
import pathlib
import re
import sqlite3
import zipfile

HOME = pathlib.Path.home()
OUT = pathlib.Path(__file__).resolve().parent.parent
DOCS = HOME / ".qbot/research-data/correction-impact/docs"
DB = f"file:{HOME}/.qbot/qbot-paper.sqlite3?mode=ro"
UNITS = {"백만원": 1_000_000, "천원": 1_000, "억원": 100_000_000, "원": 1}
ITEMS = ["revenue", "operating_income", "net_income", "equity"]


def text(x: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()


def hangul(s: str) -> str:
    return re.sub(r"[^가-힣]", "", s)


def num(cell: str) -> float | None:
    s = text(cell).replace(",", "").replace(" ", "")
    if s in ("-", "－", "—"):
        return 0.0
    neg = s.startswith(("(", "△", "▲", "-", "－")) or s.endswith(")")
    s = re.sub(r"[()△▲\-－]", "", s)
    if not re.fullmatch(r"\d+(\.\d+)?", s) or len(s.split(".")[0]) > 15:
        return None  # 숫자가 아니거나 칸 여러 개가 붙어 읽힌 것(15자리 넘는 값)
    return -float(s) if neg else float(s)


def item_of(label: str) -> str | None:
    h = hangul(label)
    if not h:
        return None
    if h.startswith(("매출액", "영업수익")) or h in ("수익매출액", "매출"):
        return "revenue"
    if h.startswith(("영업이익", "영업손익", "영업손실")):
        return "operating_income"
    if re.match(r"^(연결)?(당기|분기|반기)순(이익|손익|손실)", h) and not any(k in h for k in ("지배", "귀속")):
        return "net_income"
    if h.startswith("자본총계"):
        return "equity"
    return None


def find_unit(s: str) -> int | None:
    m = None
    for m in re.finditer(r"단위\s*[:：]?\s*(백만원|천원|억원|원)", s):
        pass
    return UNITS[m.group(1)] if m else None


def parse(xml: str, consolidated: int) -> tuple[dict, str]:
    titles = [m for m in re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", xml, re.S)]
    sec = None
    for i, m in enumerate(titles):
        if "요약재무정보" in text(m.group(1)) and "ATOC" in m.group(0):
            end = titles[i + 1].start() if i + 1 < len(titles) else len(xml)
            sec = xml[m.end() : end]
            break
    if sec is None:
        return {}, "요약재무정보 절 없음"
    tables = list(re.finditer(r"<TABLE.*?</TABLE>", sec, re.S))
    prev_end = 0
    chosen = None
    # 제목("가. 요약연결재무정보")과 단위는 표 앞에 한 번만 나오고, 머리 표와 본문 표가 나뉘기도 한다.
    # 그래서 앞에서 읽은 제목·단위를 다음 표로 넘긴다
    cur_cons, cur_unit = None, None
    for t in tables:
        pre = text(sec[prev_end : t.start()])
        prev_end = t.end()
        body = t.group(0)
        head = text(body)[:300]
        for chunk in (pre, head):
            if "연결" in chunk and "재무" in chunk:
                cur_cons = True
            elif "별도" in chunk or re.search(r"(나|다)\.\s*요약\s*재무", chunk) or (
                "요약재무정보" in chunk.replace(" ", "") and "연결" not in chunk
            ):
                cur_cons = False
            u = find_unit(chunk)
            if u:
                cur_unit = u
        rows = re.findall(r"<TR.*?</TR>", body, re.S)
        vals = {}
        for r in rows:
            cells = re.findall(r"<T[DHEU][^>]*>(.*?)</T[DHEU]>", r, re.S)
            if len(cells) < 2:
                continue
            it = item_of(text(cells[0]))
            if it and it not in vals:
                v = next((num(c) for c in cells[1:] if num(c) is not None), None)
                if v is not None:
                    vals[it] = v
        if not vals:
            continue
        is_cons = bool(cur_cons) if cur_cons is not None else False
        if bool(consolidated) != is_cons:
            continue
        if cur_unit is None:
            return {}, "단위 없음"
        chosen = {k: v * cur_unit for k, v in vals.items()}
        break
    if chosen is None:
        return {}, "맞는 표 없음(연결/별도)"
    return chosen, "ok"


def december_fy(title: str) -> bool:
    m = re.search(r"\((\d{4})\.(\d{2})\)", title)
    if not m:
        return False
    mm = m.group(2)
    if "사업보고서" in title:
        return mm == "12"
    if "반기보고서" in title:
        return mm == "06"
    if "분기보고서" in title:
        return mm in ("03", "09")
    return False


def main() -> None:
    c = sqlite3.connect(DB, uri=True)
    sample = list(csv.DictReader(open(OUT / "sample.csv")))
    out = []
    for s in sample:
        z = DOCS / f"{s['rcept_no']}.zip"
        snap = c.execute(
            "select period_kind, consolidated, revenue, operating_income, net_income, equity from financial_snapshot"
            " where filing_id=? order by period_kind='annual' desc, id limit 1",
            (int(s["filing_id"]),),
        ).fetchone()
        base = {k: s[k] for k in ("rcept_no", "kind", "title", "gap_days", "gap_band", "year")}
        if s["gap_band"] == "neg":
            out.append({**base, "status": "제외: 정정본이 원본보다 앞선 이상 사례"})
            continue
        if not december_fy(s["title"]):
            # 봇은 보고서 종류로 기간을 정한다(사업=12월, 반기=6월...). 결산월이 12월이 아닌 회사는 봇 DB의
            # 기간이 문서와 달라 비교할 수 없다 — 이것 자체가 봇의 별도 버그다(README 참고)
            out.append({**base, "status": "제외: 결산월 12월 아님"})
            continue
        if not z.exists() or snap is None:
            out.append({**base, "status": "문서 없음" if not z.exists() else "DB 행 없음"})
            continue
        zf = zipfile.ZipFile(z)
        names = zf.namelist()  # 본문은 "<접수번호>.xml". "_00760.xml" 같은 것은 첨부(감사보고서 등)다
        main_xml = f"{s['rcept_no']}.xml" if f"{s['rcept_no']}.xml" in names else names[0]
        xml = zf.read(main_xml).decode("utf-8", "replace")
        vals, status = parse(xml, snap[1])
        row = {**base, "status": status, "consolidated": snap[1]}
        is_q1 = "(" in s["title"] and s["title"].rstrip(")").endswith(".03") and "분기" in s["title"]
        compare_pl = "사업보고서" in s["title"] or is_q1
        for i, k in enumerate(ITEMS):
            db = snap[2 + i]
            doc = vals.get(k)
            if k != "equity" and not compare_pl:
                continue
            row[f"{k}_db"], row[f"{k}_doc"] = db, doc
            if db is not None and doc is not None:
                rel = abs(doc - db) / max(abs(db), 1)
                row[f"{k}_rel"] = round(rel, 6)
                row[f"{k}_sign"] = int((doc < 0) != (db < 0) and doc != 0 and db != 0)
        out.append(row)
    keys = sorted({k for r in out for k in r}, key=lambda k: (k not in ("rcept_no", "kind", "title", "gap_days", "gap_band", "year", "status", "consolidated"), k))
    with open(OUT / "compare.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(out)
    ok = [r for r in out if r["status"] == "ok"]
    eligible = [r for r in out if not r["status"].startswith("제외")]
    print(f"표본 {len(out)}, 비교 대상 {len(eligible)}, 파싱 성공 {len(ok)} ({len(ok) / len(eligible):.0%})")
    from collections import Counter

    print("실패 사유", Counter(r["status"] for r in out if r["status"] != "ok"))
    summ = []
    for band in ["all", "0-30", "31-90", "91-365", "366+"]:
        for k in ITEMS:
            rs = [r for r in ok if (band == "all" or r["gap_band"] == band) and f"{k}_rel" in r]
            if not rs:
                continue
            changed = [r for r in rs if r[f"{k}_rel"] > 0.005]
            big = [r for r in rs if r[f"{k}_rel"] > 0.05]
            summ.append(
                dict(band=band, item=k, n=len(rs), changed_gt_0_5pct=len(changed), changed_gt_5pct=len(big),
                     sign_flips=sum(r[f"{k}_sign"] for r in rs),
                     share_changed=round(len(changed) / len(rs), 3))
            )
    with open(OUT / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0]))
        w.writeheader()
        w.writerows(summ)
    for r in summ:
        print(r)


if __name__ == "__main__":
    main()

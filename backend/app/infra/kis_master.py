"""한국투자증권 종목 마스터 파일(kospi_code.mst, kosdaq_code.mst). 전 종목의 이름·구분·지정 상태·상장주식수.

공식 파서(open-trading-api/stocks_info)의 고정폭 규격을 옮겼다. 공식 파서는 개행을 포함해 228/222자를 자르므로
개행을 뗀 여기서는 227/221자다. 하루 한 번 내려받는다.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import httpx

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

KOSPI_WIDTHS = [
    2,
    1,
    4,
    4,
    4,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
]
KOSPI_IDX = {
    "group": 0,
    "halted": 34,
    "liquidation": 35,
    "managed": 36,
    "warn": 37,
    "listed_on": 49,
    "shares_k": 50,
    "preferred": 54,
}
KOSDAQ_WIDTHS = [
    2,
    1,
    4,
    4,
    4,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
]
KOSDAQ_IDX = {
    "group": 0,
    "halted": 29,
    "liquidation": 30,
    "managed": 31,
    "warn": 32,
    "listed_on": 44,
    "shares_k": 45,
    "preferred": 49,
}


@dataclass(frozen=True)
class MasterRow:
    code: str
    name: str
    market: str  # KOSPI / KOSDAQ
    kind: str  # common / preferred / etf / other
    halted: bool
    liquidation: bool
    managed: bool
    warn_code: str  # 00 없음 01 주의 02 경고 03 위험
    listed_on: str | None
    shares_outstanding: int | None


def _split(fields: list[str], line: str, tail: int) -> tuple[str, str, str, list[str]]:
    head, rest = line[: len(line) - tail], line[-tail:]
    code, std, name = head[0:9].rstrip(), head[9:21].rstrip(), head[21:].strip()
    out, pos = [], 0
    for w in fields:
        out.append(rest[pos : pos + w])
        pos += w
    return code, std, name, out


def _kind(group: str, preferred: str) -> str:
    if group == "EF":
        return "etf"
    if group != "ST":
        return "other"
    return "preferred" if preferred.strip() not in ("", "0") else "common"


def parse(raw: bytes, market: str) -> list[MasterRow]:
    widths, idx, tail = (KOSPI_WIDTHS, KOSPI_IDX, 227) if market == "KOSPI" else (KOSDAQ_WIDTHS, KOSDAQ_IDX, 221)
    rows = []
    for bline in raw.split(b"\n"):
        if len(bline) < tail + 21:
            continue
        line = bline.decode("cp949", errors="replace").rstrip("\r")
        code, _, name, f = _split(widths, line, tail)
        if len(code) != 6 or not code.isdigit():
            continue
        shares_k = f[idx["shares_k"]].strip()
        listed = f[idx["listed_on"]].strip()
        rows.append(
            MasterRow(
                code=code,
                name=name,
                market=market,
                kind=_kind(f[idx["group"]], f[idx["preferred"]]),
                halted=f[idx["halted"]] == "Y",
                liquidation=f[idx["liquidation"]] == "Y",
                managed=f[idx["managed"]] == "Y",
                warn_code=f[idx["warn"]].strip() or "00",
                listed_on=f"{listed[:4]}-{listed[4:6]}-{listed[6:8]}" if len(listed) == 8 else None,
                shares_outstanding=int(shares_k) * 1000 if shares_k.isdigit() else None,
            )
        )
    return rows


def download(url: str, timeout: float = 60.0) -> bytes:
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return z.read(z.namelist()[0])


def fetch_all() -> list[MasterRow]:
    return parse(download(KOSPI_URL), "KOSPI") + parse(download(KOSDAQ_URL), "KOSDAQ")

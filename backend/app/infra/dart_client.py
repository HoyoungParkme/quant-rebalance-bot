"""전자공시 오픈API 클라이언트. 공시 목록, 회사 코드 표, 단일회사 전체 재무제표."""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.errors import BrokerUnavailable

BASE = "https://opendart.fss.or.kr/api"
REPRT = {"11013": "1Q", "11012": "2Q", "11014": "3Q", "11011": "FY"}
ITEMS = {
    "ifrs-full_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_owner",
    "ifrs-full_Equity": "equity",
    "ifrs-full_EquityAttributableToOwnersOfParent": "equity_owner",
}


@dataclass
class DartClient:
    api_key: str
    corp_cache: Path | None = None
    timeout: float = 20.0
    _http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20.0), init=False, repr=False)

    def _get(self, path: str, **params) -> dict:
        last = None
        for attempt in range(4):
            try:
                r = self._http.get(f"{BASE}/{path}", params={"crtfc_key": self.api_key, **params})
                if r.status_code >= 500:
                    raise httpx.HTTPError(f"{r.status_code}")
                body = r.json()
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if body.get("status") == "020":  # 일일 한도 초과
                raise BrokerUnavailable("전자공시 일일 호출 한도 초과")
            return body
        raise BrokerUnavailable(f"전자공시 조회 실패 {path}: {last}")

    def list_filings(self, bgn: str, end: str, pblntf_ty: str = "A") -> list[dict]:
        """접수일 bgn~end(YYYYMMDD)의 공시 목록. 정기공시(A)가 기본. 페이지를 끝까지 넘긴다."""
        out, page = [], 1
        while True:
            b = self._get("list.json", bgn_de=bgn, end_de=end, pblntf_ty=pblntf_ty, page_count=100, page_no=page)
            if b.get("status") == "013":  # 조회 결과 없음
                return out
            if b.get("status") != "000":
                raise BrokerUnavailable(f"list.json {b.get('status')} {b.get('message')}")
            out += b.get("list", [])
            if page >= int(b.get("total_page", 1)):
                return out
            page += 1

    def corp_codes(self) -> dict[str, str]:
        """{종목코드: corp_code}. zip 28MB라 하루 캐시."""
        if self.corp_cache and self.corp_cache.exists() and time.time() - self.corp_cache.stat().st_mtime < 86400:
            return json.loads(self.corp_cache.read_text())
        r = self._http.get(f"{BASE}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=120)
        r.raise_for_status()
        xml = zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml").decode("utf-8")
        out = parse_corp_codes(xml)
        if self.corp_cache:
            self.corp_cache.parent.mkdir(parents=True, exist_ok=True)
            self.corp_cache.write_text(json.dumps(out))
        return out

    def financials(self, corp_code: str, year: str, reprt_code: str) -> dict | None:
        """단일회사 전체 재무제표에서 필요한 항목만. 연결(CFS) 우선, 없으면 별도(OFS).

        반환: {"rcept_no", "consolidated", "revenue", "operating_income", "net_income", "net_income_owner",
               "equity", "equity_owner", "cum": {…누적}} 또는 None(자료 없음).
        """
        for fs_div, cons in (("CFS", 1), ("OFS", 0)):
            b = self._get(
                "fnlttSinglAcntAll.json",
                corp_code=corp_code,
                bsns_year=year,
                reprt_code=reprt_code,
                fs_div=fs_div,
            )
            rows = b.get("list") or []
            if b.get("status") == "013" or not rows:
                continue
            got = {"rcept_no": rows[0]["rcept_no"], "consolidated": cons, "cum": {}}
            for r in rows:
                key = ITEMS.get(r.get("account_id"))
                if not key or key in got:
                    continue
                if key in ("equity", "equity_owner") and r.get("sj_div") != "BS":
                    continue
                if key not in ("equity", "equity_owner") and r.get("sj_div") not in ("IS", "CIS"):
                    continue
                got[key] = _num(r.get("thstrm_amount"))
                if r.get("thstrm_add_amount") not in (None, ""):
                    got["cum"][key] = _num(r.get("thstrm_add_amount"))
            if any(k in got for k in ("revenue", "operating_income", "net_income", "equity")):
                return got
        return None


def parse_corp_codes(xml: str) -> dict[str, str]:
    """<list> 블록 하나 안에서만 corp_code와 stock_code를 짝짓는다. 비상장사는 stock_code가 비어 있다."""
    out = {}
    for block in re.findall(r"<list>(.*?)</list>", xml, flags=re.S):
        c = re.search(r"<corp_code>(\d+)</corp_code>", block)
        s = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", block)
        if c and s:
            out[s.group(1)] = c.group(1)
    return out


def _num(v) -> int | None:
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None

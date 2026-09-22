"""FilingPort의 전자공시 구현."""

from __future__ import annotations

import re

from app.domains.marketdata.ports import FilingInfo, FinancialRows
from app.infra.dart_client import DartClient

KIND_RE = [
    (re.compile(r"사업보고서"), "annual", "11011"),
    (re.compile(r"반기보고서"), "half", "11012"),
    (re.compile(r"분기보고서"), "quarter", None),  # 1Q/3Q는 월로 가른다
]


def classify(title: str) -> tuple[str, str | None, str | None]:
    """제목 → (report_kind, reprt_code, 사업연도). 예: '[기재정정]분기보고서 (2025.03)'."""
    m = re.search(r"\((\d{4})\.(\d{2})\)", title)
    year, month = (m.group(1), m.group(2)) if m else (None, None)
    for rx, kind, code in KIND_RE:
        if rx.search(title):
            if kind == "quarter":
                code = "11013" if month in ("03", "01", "02", "04", "05") else "11014"
            if "정정" in title:
                return "correction", code, year
            return kind, code, year
    return "other", None, year


class DartAdapter:
    def __init__(self, client: DartClient) -> None:
        self.c = client

    def list_filings(self, frm: str, to: str) -> list[FilingInfo]:
        codes = self.c.corp_codes()
        corp_to_stock = {v: k for k, v in codes.items()}
        out = []
        for r in self.c.list_filings(frm.replace("-", ""), to.replace("-", ""), "A"):
            stock = (r.get("stock_code") or "").strip() or corp_to_stock.get(r["corp_code"], "")
            if not stock:
                continue
            kind, reprt, year = classify(r["report_nm"])
            d = r["rcept_dt"]
            out.append(
                FilingInfo(
                    r["rcept_no"],
                    stock,
                    r["corp_code"],
                    kind,
                    reprt,
                    year,
                    f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    r["report_nm"],
                )
            )
        return out

    def financials(self, corp_code: str, year: str, reprt_code: str) -> FinancialRows | None:
        got = self.c.financials(corp_code, year, reprt_code)
        if not got:
            return None
        vals = {k: v for k, v in got.items() if k not in ("rcept_no", "consolidated", "cum")}
        return FinancialRows(got["rcept_no"], got["consolidated"], vals, got.get("cum", {}))

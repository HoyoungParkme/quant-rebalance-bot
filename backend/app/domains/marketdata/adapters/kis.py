"""BrokerPort의 한국투자증권 구현 (조회 부분)."""

from __future__ import annotations

from datetime import date, timedelta

from app.domains.marketdata.ports import Bar, CalendarDay, InstrumentInfo
from app.infra import kis_master
from app.infra.kis_client import KisClient

INDEX_CODES = {"KOSPI": "0001", "KOSDAQ": "1001", "KOSPI200": "2001"}


class KisAdapter:
    def __init__(self, client: KisClient, master_fetch=kis_master.fetch_all) -> None:
        self.c = client
        self._master_fetch = master_fetch

    def daily_bars(self, code: str, frm: str, to: str) -> list[Bar]:
        """일봉. 한 번에 100개라 뒤에서부터 페이지를 넘긴다. 수정주가(FID_ORG_ADJ_PRC=0)."""
        out: dict[str, Bar] = {}
        end = to
        while True:
            body = self.c.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": frm.replace("-", ""),
                    "FID_INPUT_DATE_2": end.replace("-", ""),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            rows = [r for r in body.get("output2", []) if r.get("stck_bsop_date")]
            if not rows:
                break
            for r in rows:
                d = r["stck_bsop_date"]
                out[d] = Bar(
                    date=f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    open=int(r["stck_oprc"] or 0),
                    high=int(r["stck_hgpr"] or 0),
                    low=int(r["stck_lwpr"] or 0),
                    close=int(r["stck_clpr"] or 0),
                    volume=int(r["acml_vol"] or 0),
                    amount=int(r["acml_tr_pbmn"] or 0),
                    adjusted=r.get("mod_yn") == "Y",
                )
            oldest = min(rows, key=lambda r: r["stck_bsop_date"])["stck_bsop_date"]
            if len(rows) < 100 or oldest <= frm.replace("-", ""):
                break
            end = (date.fromisoformat(f"{oldest[:4]}-{oldest[4:6]}-{oldest[6:]}") - timedelta(days=1)).isoformat()
        return sorted(out.values(), key=lambda b: b.date)

    def current_price(self, code: str) -> int:
        """현재가. 장 시작 전에는 전일 종가가 온다. 매수 수량 계산에 쓴다."""
        body = self.c.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        out = body.get("output") or {}
        return int(float(out.get("stck_prpr") or 0))

    def instrument_list(self) -> list[InstrumentInfo]:
        out = []
        for m in self._master_fetch():
            st = set()
            if m.managed:
                st.add("managed")
            if m.halted:
                st.add("halted")
            if m.liquidation:
                st.add("liquidation")
            st |= {"01": {"caution"}, "02": {"warning"}, "03": {"danger"}}.get(m.warn_code, set())
            out.append(
                InstrumentInfo(m.code, m.name, m.market, m.kind, m.listed_on, m.shares_outstanding, frozenset(st))
            )
        return out

    def calendar(self, frm: str, to: str) -> list[CalendarDay]:
        """휴장일 조회(CTCA0903R). 실전 키에서만 된다. 기준일부터 약 한 달씩 넘어간다."""
        out: dict[str, bool] = {}
        cur = frm
        while cur <= to:
            body = self.c.get(
                "/uapi/domestic-stock/v1/quotations/chk-holiday",
                "CTCA0903R",
                {"BASS_DT": cur.replace("-", ""), "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
            )
            rows = body.get("output", [])
            if not rows:
                break
            for r in rows:
                d = r["bass_dt"]
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = r.get("opnd_yn") == "Y"
            last = max(r["bass_dt"] for r in rows)
            nxt = date.fromisoformat(f"{last[:4]}-{last[4:6]}-{last[6:]}") + timedelta(days=1)
            if nxt.isoformat() <= cur:
                break
            cur = nxt.isoformat()
        return [CalendarDay(d, v) for d, v in sorted(out.items()) if frm <= d <= to]

    def index_closes(self, index_name: str, frm: str, to: str) -> dict[str, float]:
        """업종 지수 일별 종가. 뒤에서부터 페이지를 넘긴다.

        한 페이지 크기를 가정하지 않는다. 일봉은 100개씩이지만 지수는 50개씩 와서,
        "100개 미만이면 끝"으로 짜면 첫 페이지(두 달 남짓)에서 멈춘다.
        그러면 추세 필터가 10개월이 아니라 있는 만큼(2~3개월) 평균으로 판단한다.
        """
        out: dict[str, float] = {}
        end = to
        prev_oldest = None
        while True:
            body = self.c.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": INDEX_CODES[index_name],
                    "FID_INPUT_DATE_1": frm.replace("-", ""),
                    "FID_INPUT_DATE_2": end.replace("-", ""),
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            rows = [r for r in body.get("output2", []) if r.get("stck_bsop_date")]
            if not rows:
                break
            for r in rows:
                d = r["stck_bsop_date"]
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = float(r["bstp_nmix_prpr"])
            oldest = min(r["stck_bsop_date"] for r in rows)
            if oldest <= frm.replace("-", "") or oldest == prev_oldest:  # 다 받았거나 더 오래된 자료가 없다
                break
            prev_oldest = oldest
            end = (date.fromisoformat(f"{oldest[:4]}-{oldest[4:6]}-{oldest[6:]}") - timedelta(days=1)).isoformat()
        return {d: c for d, c in sorted(out.items()) if frm <= d <= to}

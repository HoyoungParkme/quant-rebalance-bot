"""OrderBrokerPort의 한국투자증권 구현.

거래 ID(tr_id)는 모의와 실전이 다르다. 모의 접속 주소에 실전 tr_id를 보내면 거부되고,
그 반대도 거부된다. 즉 모드가 틀리면 주문이 나가는 게 아니라 실패한다 (QBOT-INFRA-001 C6).
"""

from __future__ import annotations

from app.core.clock import Clock
from app.domains.trading.ports import Balance, BrokerOrder, Holding, OrderRequest
from app.infra.kis_client import KisClient

LIVE_TR = {"buy": "TTTC0802U", "sell": "TTTC0801U", "cancel": "TTTC0803U", "ccld": "TTTC8001R", "bal": "TTTC8434R"}
PAPER_TR = {"buy": "VTTC0802U", "sell": "VTTC0801U", "cancel": "VTTC0803U", "ccld": "VTTC8001R", "bal": "VTTC8434R"}

ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"

SIDE_OF = {"01": "sell", "02": "buy"}  # sll_buy_dvsn_cd


def _num(v) -> int:
    """증권사 숫자 문자열 → 정수. 빈 값·소수점 문자열을 견딘다."""
    try:
        return int(float(str(v).strip() or 0))
    except ValueError:
        return 0


def _norm(no: str) -> str:
    """주문번호는 응답마다 앞자리 0이 붙기도 한다. 비교는 0을 뗀 값으로 한다."""
    return (no or "").strip().lstrip("0") or "0"


class KisOrderAdapter:
    """모의투자는 주문별 체결 내역을 주지 않는다(합계만 준다).

    `inquire-daily-ccld`의 output1이 늘 비어 있고 정정취소가능주문조회는 아예 막혀 있다.
    그래서 모의에서는 **잔고 변화와 그날 체결 합계**로 체결을 재구성한다. 그러지 않으면
    봇이 자기 체결을 기록하지 못해 보유·예수금 대조가 매일 어긋난다.
    """

    def __init__(self, client: KisClient, account: str, product: str, mode: str, clock: Clock) -> None:
        self.c = client
        self.account = account
        self.product = product
        self.tr = LIVE_TR if mode == "live" else PAPER_TR
        self.clock = clock
        self._placed: dict[str, dict] = {}  # 주문번호 → 보낼 때의 잔고. 모의 체결 재구성용

    def _acct(self) -> dict:
        return {"CANO": self.account, "ACNT_PRDT_CD": self.product}

    # ----- 주문 -----
    def place_order(self, req: OrderRequest) -> str:
        limit = req.order_type == "limit"
        body = {
            **self._acct(),
            "PDNO": req.code,
            "ORD_DVSN": "00" if limit else "01",  # 00 지정가, 01 시장가
            "ORD_QTY": str(req.qty),
            "ORD_UNPR": str(req.price if limit else 0),
        }
        before = self._snapshot(req.code)  # 보내기 전 잔고. 모의에서 체결을 재구성할 때 쓴다
        out = self.c.post(ORDER_PATH, self.tr[req.side], body).get("output") or {}
        no = out.get("ODNO", "")
        if not no.strip():
            raise ValueError(f"주문번호가 비었다: {out}")
        no = _norm(no)
        self._placed[no] = {"code": req.code, "side": req.side, "qty": req.qty, **before}
        return no

    def _snapshot(self, code: str) -> dict:
        """그 종목의 지금 보유 수량·매입원가."""
        h = self.balance().holdings.get(code)
        return {"qty_before": h.qty if h else 0, "cost_before": (h.qty * h.avg_cost) if h else 0}

    def _day_side_avg(self, side: str) -> int:
        """오늘 그 방향의 평균 체결가. 종목별로는 섞이지만 **합계는 정확하다**.

        여러 종목을 같은 날 팔면 종목별 값은 뭉뚱그려지지만, 수량 가중 평균이라
        Σ(수량×평균) = Σ(수량×실제)가 되어 예수금 대조가 어긋나지 않는다.
        """
        d = self.clock.today().isoformat().replace("-", "")
        body = self.c.get(
            CCLD_PATH,
            self.tr["ccld"],
            {
                **self._acct(),
                "INQR_STRT_DT": d,
                "INQR_END_DT": d,
                "SLL_BUY_DVSN_CD": "01" if side == "sell" else "02",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        agg = body.get("output2") or [{}]
        agg = agg[0] if isinstance(agg, list) else agg
        qty, amt = _num(agg.get("tot_ccld_qty")), _num(agg.get("tot_ccld_amt"))
        return round(amt / qty) if qty else 0

    def _reconstruct(self, order_no: str) -> BrokerOrder | None:
        """잔고 변화로 체결을 재구성한다 (모의투자 전용 경로)."""
        p = self._placed.get(order_no)
        if p is None:
            return None
        h = self.balance().holdings.get(p["code"])
        now_qty, now_cost = (h.qty, h.qty * h.avg_cost) if h else (0, 0)
        moved = (now_qty - p["qty_before"]) if p["side"] == "buy" else (p["qty_before"] - now_qty)
        filled = max(min(moved, p["qty"]), 0)
        if filled == 0:
            price = 0
        elif p["side"] == "buy":
            price = round((now_cost - p["cost_before"]) / filled)  # 매입원가 증가분이 곧 체결 금액
        else:
            price = self._day_side_avg("sell")
        return BrokerOrder(order_no, p["code"], p["side"], p["qty"], filled, price, raw_no=order_no)

    def cancel_order(self, order_no: str) -> bool:
        """전량 취소. 이미 체결·취소됐으면 False."""
        cur = self._find(order_no)
        if cur is None or cur.cancelled or cur.filled_qty >= cur.qty:
            return False
        body = {
            **self._acct(),
            "KRX_FWDG_ORD_ORGNO": cur.org_no,
            "ORGN_ODNO": cur.raw_no,
            "ORD_DVSN": "01",
            "RVSE_CNCL_DVSN_CD": "02",  # 02 취소
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        self.c.post(CANCEL_PATH, self.tr["cancel"], body)
        return True

    def order_status(self, order_no: str) -> BrokerOrder | None:
        found = self._find(order_no)
        return found if found is not None else self._reconstruct(order_no)

    def _find(self, order_no: str) -> BrokerOrder | None:
        want = _norm(order_no)
        return next((o for o in self.orders_today() if o.order_no == want), None)

    def orders_today(self) -> list[BrokerOrder]:
        d = self.clock.today().isoformat().replace("-", "")
        out: list[BrokerOrder] = []
        fk, nk, cont = "", "", ""
        while True:
            body = self.c.get(
                CCLD_PATH,
                self.tr["ccld"],
                {
                    **self._acct(),
                    "INQR_STRT_DT": d,
                    "INQR_END_DT": d,
                    "SLL_BUY_DVSN_CD": "00",  # 전체
                    "INQR_DVSN": "00",
                    "PDNO": "",
                    "CCLD_DVSN": "00",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": fk,
                    "CTX_AREA_NK100": nk,
                },
                tr_cont=cont,
            )
            for r in body.get("output1") or []:
                if not (r.get("odno") or "").strip():
                    continue
                qty = _num(r.get("ord_qty"))
                filled = _num(r.get("tot_ccld_qty"))
                out.append(
                    BrokerOrder(
                        order_no=_norm(r["odno"]),
                        code=(r.get("pdno") or "").strip(),
                        side=SIDE_OF.get((r.get("sll_buy_dvsn_cd") or "").strip(), "buy"),
                        qty=qty,
                        filled_qty=filled,
                        avg_price=_num(r.get("avg_prvs")),
                        # 취소 표시가 없는 응답도 있다. 잔량이 0인데 체결도 안 됐으면 취소된 것이다
                        cancelled=(r.get("cncl_yn") or "N").strip() == "Y"
                        or (filled < qty and _num(r.get("rmn_qty")) == 0),
                        raw_no=r["odno"].strip(),
                        org_no=(r.get("ord_gno_brno") or "").strip(),
                    )
                )
            if (body.get("_tr_cont") or "") not in ("F", "M"):
                return out
            fk, nk, cont = body.get("ctx_area_fk100", ""), body.get("ctx_area_nk100", ""), "N"

    # ----- 잔고 -----
    def balance(self) -> Balance:
        holdings: dict[str, Holding] = {}
        cash = total = 0
        fk, nk, cont = "", "", ""
        while True:
            body = self.c.get(
                BALANCE_PATH,
                self.tr["bal"],
                {
                    **self._acct(),
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",  # 종목별
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": fk,
                    "CTX_AREA_NK100": nk,
                },
                tr_cont=cont,
            )
            for r in body.get("output1") or []:
                qty = _num(r.get("hldg_qty"))
                code = (r.get("pdno") or "").strip()
                if qty > 0 and code:
                    holdings[code] = Holding(code, qty, _num(r.get("pchs_avg_pric")), _num(r.get("evlu_amt")))
            rows2 = body.get("output2") or []
            if rows2:  # 이어보기 페이지에는 요약이 없을 수 있다. 있을 때만 갱신한다
                o2 = rows2[0]
                # 예수금은 D+2 정산금액을 쓴다. 오늘 판 대금은 아직 현금이 아니다
                cash = _num(o2.get("prvs_rcdl_excc_amt")) or _num(o2.get("dnca_tot_amt"))
                total = _num(o2.get("tot_evlu_amt")) or _num(o2.get("nass_amt"))
            if (body.get("_tr_cont") or "") not in ("F", "M"):
                break
            fk, nk, cont = body.get("ctx_area_fk100", ""), body.get("ctx_area_nk100", ""), "N"
        if total == 0:  # 잔고가 비면 총평가금액이 0으로 오기도 한다
            total = cash + sum(h.value for h in holdings.values())
        return Balance(cash=cash, total_equity=total, holdings=holdings)

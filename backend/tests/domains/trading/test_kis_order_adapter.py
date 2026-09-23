"""KisOrderAdapter — 요청 구성과 응답 해석. 실제 호출은 tests/live."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import KST, FrozenClock
from app.domains.trading.adapters.kis import KisOrderAdapter
from app.domains.trading.ports import OrderRequest

CLOCK = FrozenClock(datetime(2026, 9, 22, 10, 0, tzinfo=KST))


@dataclass
class FakeKis:
    posts: list = field(default_factory=list)
    gets: list = field(default_factory=list)
    ccld_pages: list = field(default_factory=list)
    balance_body: dict = field(default_factory=dict)

    def post(self, path, tr_id, body):
        self.posts.append((path, tr_id, body))
        return {"rt_cd": "0", "output": {"ODNO": "0000012345", "KRX_FWDG_ORD_ORGNO": "91252"}}

    def get(self, path, tr_id, params, tr_cont=""):
        self.gets.append((path, tr_id, params, tr_cont))
        if "inquire-balance" in path:
            return self.balance_body
        return self.ccld_pages.pop(0) if self.ccld_pages else {"output1": [], "output2": [{}], "_tr_cont": "D"}


def adapter(c, mode="paper"):
    return KisOrderAdapter(c, "50212906", "01", mode, CLOCK)


def ccld(**kw):
    row = {
        "odno": "0000012345",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "3",
        "tot_ccld_qty": "3",
        "avg_prvs": "70500",
        "rmn_qty": "0",
        "cncl_yn": "N",
        "ord_gno_brno": "91252",
    }
    return {**row, **kw}


def test_market_buy_body_and_paper_tr_id():
    c = FakeKis()
    no = adapter(c).place_order(OrderRequest("005930", "buy", 3, 70_000))
    path, tr, body = c.posts[0]
    assert tr == "VTTC0802U" and path.endswith("order-cash")
    assert body["ORD_DVSN"] == "01" and body["ORD_UNPR"] == "0" and body["ORD_QTY"] == "3"
    assert body["CANO"] == "50212906" and no == "12345"  # 앞자리 0을 뗀 번호로 다룬다


def test_limit_sell_uses_price_and_live_tr_id():
    c = FakeKis()
    adapter(c, "live").place_order(OrderRequest("005930", "sell", 1, 70_000, "limit"))
    _, tr, body = c.posts[0]
    assert tr == "TTTC0801U" and body["ORD_DVSN"] == "00" and body["ORD_UNPR"] == "70000"


def test_orders_today_parses_and_follows_pages():
    c = FakeKis()
    c.ccld_pages = [
        {"output1": [ccld()], "_tr_cont": "F", "ctx_area_fk100": "FK", "ctx_area_nk100": "NK"},
        {"output1": [ccld(odno="0000012346", sll_buy_dvsn_cd="01", tot_ccld_qty="0", rmn_qty="3")], "_tr_cont": "D"},
    ]
    out = adapter(c).orders_today()
    assert [o.order_no for o in out] == ["12345", "12346"]
    assert out[0].side == "buy" and out[0].filled_qty == 3 and out[0].avg_price == 70500
    assert out[1].side == "sell" and out[1].cancelled is False  # 잔량이 남았으면 취소가 아니다
    assert c.gets[1][2]["CTX_AREA_FK100"] == "FK" and c.gets[1][3] == "N"


def test_zero_remaining_without_fill_is_cancelled():
    c = FakeKis()
    c.ccld_pages = [{"output1": [ccld(tot_ccld_qty="0", rmn_qty="0")], "_tr_cont": "D"}]
    assert adapter(c).orders_today()[0].cancelled is True


def test_cancel_sends_original_order_no_and_skips_filled():
    c = FakeKis()
    c.ccld_pages = [{"output1": [ccld(tot_ccld_qty="0", rmn_qty="3")], "_tr_cont": "D"}]
    assert adapter(c).cancel_order("12345") is True
    _, tr, body = c.posts[0]
    assert tr == "VTTC0803U" and body["ORGN_ODNO"] == "0000012345" and body["RVSE_CNCL_DVSN_CD"] == "02"
    assert body["KRX_FWDG_ORD_ORGNO"] == "91252"  # 체결조회에서 받은 주문조직번호를 그대로 넣는다

    c2 = FakeKis()
    c2.ccld_pages = [{"output1": [ccld()], "_tr_cont": "D"}]  # 전량 체결
    assert adapter(c2).cancel_order("12345") is False and c2.posts == []


def test_balance_parses_holdings_and_cash():
    c = FakeKis()
    c.balance_body = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "70000.00", "evlu_amt": "211500"},
            {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0", "evlu_amt": "0"},
        ],
        "output2": [{"prvs_rcdl_excc_amt": "788500", "tot_evlu_amt": "1000000", "dnca_tot_amt": "800000"}],
        "_tr_cont": "D",
    }
    b = adapter(c).balance()
    assert b.cash == 788_500 and b.total_equity == 1_000_000
    assert list(b.holdings) == ["005930"] and b.holdings["005930"].value == 211_500  # 0주는 뺀다


def test_balance_falls_back_when_total_is_empty():
    c = FakeKis()
    c.balance_body = {"output1": [], "output2": [{"dnca_tot_amt": "500000"}], "_tr_cont": "D"}
    b = adapter(c).balance()
    assert b.cash == 500_000 and b.total_equity == 500_000


def test_paper_fill_is_reconstructed_from_the_balance():
    """모의투자는 주문별 체결을 주지 않는다. 잔고 변화로 재구성하지 않으면 체결을 영영 모른다."""
    c = FakeKis()
    c.balance_body = {
        "output1": [],
        "output2": [{"prvs_rcdl_excc_amt": "1000000", "tot_evlu_amt": "1000000"}],
        "_tr_cont": "D",
    }
    ad = adapter(c)
    no = ad.place_order(OrderRequest("005930", "buy", 2, 70_000))  # 보내기 전 보유 0
    c.balance_body["output1"] = [{"pdno": "005930", "hldg_qty": "2", "pchs_avg_pric": "70500", "evlu_amt": "141000"}]
    st = ad.order_status(no)
    assert st is not None and st.filled_qty == 2 and st.avg_price == 70_500  # 매입원가 증가분 ÷ 수량
    assert st.code == "005930" and st.side == "buy"


def test_paper_sell_uses_the_day_average_so_totals_match():
    """종목별 값은 뭉뚱그려지지만 수량 가중 평균이라 합계는 정확하다."""
    c = FakeKis()
    c.balance_body = {
        "output1": [{"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "70000", "evlu_amt": "210000"}],
        "output2": [{"prvs_rcdl_excc_amt": "0", "tot_evlu_amt": "210000"}],
        "_tr_cont": "D",
    }
    ad = adapter(c)
    no = ad.place_order(OrderRequest("005930", "sell", 3, 71_000))
    c.balance_body["output1"] = []  # 전량 매도됨
    c.ccld_pages = [  # 첫 조회는 주문 목록(모의는 늘 비어 있다), 두 번째가 그날 매도 합계
        {"output1": [], "output2": [{}], "_tr_cont": "D"},
        {"output1": [], "output2": [{"tot_ccld_qty": "3", "tot_ccld_amt": "213000"}], "_tr_cont": "D"},
    ]
    st = ad.order_status(no)
    assert st.filled_qty == 3 and st.avg_price == 71_000  # 213,000 ÷ 3


def test_live_rows_win_over_reconstruction():
    """실전은 주문별 내역이 온다. 그때는 재구성하지 않는다."""
    c = FakeKis()
    c.balance_body = {"output1": [], "output2": [{"dnca_tot_amt": "0"}], "_tr_cont": "D"}
    ad = adapter(c, "live")
    no = ad.place_order(OrderRequest("005930", "buy", 3, 70_000))
    c.ccld_pages = [{"output1": [ccld(odno="0000012345")], "_tr_cont": "D"}]
    st = ad.order_status(no)
    assert st.filled_qty == 3 and st.avg_price == 70500 and st.raw_no == "0000012345"

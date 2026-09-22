"""주문용 증권사 인터페이스 (QBOT-DOM-002 4.4).

조회용 BrokerPort와 나눈 이유: 조회는 실전 키로 하고 주문은 모드에 따른 키로 한다
(QBOT-CODE-001 결정). 접속 정보가 다르니 클라이언트도 어댑터도 다른 것이 맞다.
DOM-002 6장 미결 "조회용과 주문용을 나눌지"는 여기서 나누는 것으로 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OrderRequest:
    code: str
    side: str  # buy sell
    qty: int
    price: int  # 시장가면 예상 체결가(관문의 금액 판정용), 지정가면 주문 가격
    order_type: str = "market"  # market limit


@dataclass(frozen=True)
class BrokerOrder:
    """증권사가 보는 주문 하나. 체결 수량은 누적값이다."""

    order_no: str
    code: str
    side: str
    qty: int
    filled_qty: int
    avg_price: int
    cancelled: bool = False
    raw_no: str = ""  # 증권사 원문 주문번호(앞자리 0 포함). 취소에 그대로 넣는다
    org_no: str = ""  # 주문 채번 지점 번호. 취소에 필요하다


@dataclass(frozen=True)
class Holding:
    code: str
    qty: int
    avg_cost: int
    value: int


@dataclass(frozen=True)
class Balance:
    cash: int  # 주문 가능 현금
    total_equity: int  # 현금 + 주식 평가
    holdings: dict[str, Holding] = field(default_factory=dict)


@dataclass(frozen=True)
class EquitySnapshot:
    """관문이 판정에 쓰는 계좌 한 장면. TradingService가 만든다."""

    cash: int
    total: int
    holdings: dict[str, int]  # 종목 → 평가액
    today_order_amount: int  # 오늘 체결 금액 합
    month_buy_amount: int  # 이번 달 매수 체결 금액 합 (실전 첫 달 상한)


class OrderBrokerPort(Protocol):
    def place_order(self, req: OrderRequest) -> str: ...
    def cancel_order(self, order_no: str) -> bool: ...
    def order_status(self, order_no: str) -> BrokerOrder | None: ...
    def orders_today(self) -> list[BrokerOrder]: ...
    def balance(self) -> Balance: ...

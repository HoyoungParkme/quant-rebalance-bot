"""외부 시스템 인터페이스 (QBOT-DOM-002 BrokerPort, FilingPort). 조회 부분. 주문 부분은 슬라이스 D1에서 붙는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    amount: int
    adjusted: bool = False  # mod_yn: 이 봉이 수정주가 반영으로 바뀌었는가


@dataclass(frozen=True)
class InstrumentInfo:
    code: str
    name: str
    market: str
    kind: str
    listed_on: str | None
    shares_outstanding: int | None
    statuses: frozenset[str] = frozenset()  # managed, halted, liquidation, caution, warning, danger


@dataclass(frozen=True)
class CalendarDay:
    date: str
    is_trading_day: bool


@dataclass(frozen=True)
class FilingInfo:
    rcept_no: str
    stock_code: str
    corp_code: str
    report_kind: str  # annual, half, quarter, correction, other
    reprt_code: str | None  # 11011 11012 11013 11014
    year: str | None
    rcept_date: str
    title: str


@dataclass
class FinancialRows:
    rcept_no: str
    consolidated: int
    values: dict = field(
        default_factory=dict
    )  # revenue, operating_income, net_income, net_income_owner, equity, equity_owner
    cumulative: dict = field(default_factory=dict)


class BrokerPort(Protocol):
    def daily_bars(self, code: str, frm: str, to: str) -> list[Bar]: ...
    def current_price(self, code: str) -> int: ...
    def instrument_list(self) -> list[InstrumentInfo]: ...
    def calendar(self, frm: str, to: str) -> list[CalendarDay]: ...
    def index_closes(self, index_name: str, frm: str, to: str) -> dict[str, float]: ...


class FilingPort(Protocol):
    def list_filings(self, frm: str, to: str) -> list[FilingInfo]: ...
    def financials(self, corp_code: str, year: str, reprt_code: str) -> FinancialRows | None: ...

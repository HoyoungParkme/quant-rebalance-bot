"""가짜 BrokerPort·FilingPort. 수집 테스트용."""

from __future__ import annotations

from app.domains.marketdata.ports import Bar, CalendarDay, FilingInfo, FinancialRows, InstrumentInfo


class FakeBroker:
    def __init__(self):
        self.instruments: list[InstrumentInfo] = []
        self.bars: dict[str, list[Bar]] = {}
        self.days: list[CalendarDay] = []
        self.index: dict[str, dict[str, float]] = {}
        self.calls: list[tuple] = []

    def daily_bars(self, code, frm, to):
        self.calls.append(("bars", code, frm, to))
        return [b for b in self.bars.get(code, []) if frm <= b.date <= to]

    def instrument_list(self):
        return list(self.instruments)

    def calendar(self, frm, to):
        return [d for d in self.days if frm <= d.date <= to]

    def index_closes(self, name, frm, to):
        return {d: c for d, c in self.index.get(name, {}).items() if frm <= d <= to}


class FakeFilings:
    def __init__(self):
        self.filings: list[FilingInfo] = []
        self.fin: dict[tuple, FinancialRows] = {}

    def list_filings(self, frm, to):
        return [f for f in self.filings if frm <= f.rcept_date <= to]

    def financials(self, corp, year, reprt):
        return self.fin.get((corp, year, reprt))


def bar(d, close, volume=1000):
    return Bar(d, close, close, close, close, volume, close * volume)

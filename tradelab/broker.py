"""IBKR-adgang for aktier/ETF'er via ib_async (brøkdele understøttes)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("tradelab.broker")


@dataclass
class Fill:
    filled: float
    avg_price: float
    done: bool


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or f <= 0 else f


class StockBroker:
    def __init__(self, cfg, ib=None):
        if ib is None:
            from ib_async import IB

            ib = IB()
        self.cfg = cfg
        self.ib = ib
        self._contracts = {}
        self._tickers = {}

    def connect(self):
        if not self.ib.isConnected():
            self.ib.connect(self.cfg.ib_host, self.cfg.ib_port, clientId=self.cfg.lab_client_id,
                            timeout=30, account=self.cfg.ib_account)
            self.ib.reqMarketDataType(self.cfg.market_data_type)

    def disconnect(self):
        try:
            self.ib.disconnect()
        except Exception:
            pass

    def sleep(self, s):
        self.ib.sleep(s)

    def accounts(self) -> list[str]:
        return list(self.ib.managedAccounts())

    def cash_usd(self) -> float:
        by = {}
        for v in self.ib.accountValues(self.cfg.ib_account):
            if v.currency == "USD":
                try:
                    by[v.tag] = float(v.value)
                except (TypeError, ValueError):
                    pass
        for tag in ("CashBalance", "TotalCashValue"):
            if tag in by:
                c = by[tag]
                if "AvailableFunds" in by:
                    c = min(c, by["AvailableFunds"])
                return max(0.0, c)
        raise RuntimeError("Ingen USD-kontant fundet")

    def positions(self, symbols) -> dict[str, float]:
        out = {s: 0.0 for s in symbols}
        for p in self.ib.positions(self.cfg.ib_account):
            c = p.contract
            if c.secType == "STK" and c.symbol in out:
                out[c.symbol] += float(p.position)
        return out

    def _contract(self, sym):
        if sym not in self._contracts:
            from ib_async import Stock

            q = self.ib.qualifyContracts(Stock(sym, "SMART", "USD"))
            if not q or not getattr(q[0], "conId", 0):
                raise RuntimeError(f"Kunne ikke kvalificere {sym}")
            self._contracts[sym] = q[0]
        return self._contracts[sym]

    def quote(self, sym) -> tuple[Optional[float], Optional[float], Optional[float]]:
        if sym not in self._tickers:
            self._tickers[sym] = self.ib.reqMktData(self._contract(sym), "", False, False)
        t = self._tickers[sym]
        for _ in range(30):
            self.ib.sleep(0.1)
            if _num(t.ask) or _num(t.last):
                break
        return _num(t.bid), _num(t.ask), _num(t.last) or _num(t.close)

    def place_limit(self, sym, action, qty, price):
        from ib_async import LimitOrder

        o = LimitOrder(action, qty, price, tif="DAY")
        if self.cfg.ib_account:
            o.account = self.cfg.ib_account
        return self.ib.placeOrder(self._contract(sym), o)

    def status(self, trade) -> Fill:
        self.ib.sleep(0)
        return Fill(float(trade.filled()), float(trade.orderStatus.avgFillPrice or 0.0), trade.isDone())

    def cancel(self, trade):
        if not trade.isDone():
            self.ib.cancelOrder(trade.order)
            for _ in range(50):
                self.ib.sleep(0.1)
                if trade.isDone():
                    break

"""Broker-abstraktion. ``IBBroker`` taler med IB Gateway via ib_async.

Motoren (engine.py) kender kun ``Broker``-interfacet, så den kan testes
med en falsk broker uden netværk.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Optional

from .calendar import ET, at
from .config import Config
from .pricing import Quote

log = logging.getLogger("xspbot.broker")


@dataclass
class OptionPosition:
    contract: Any
    qty: int
    avg_cost: float  # IB's avgCost pr. kontrakt (inkl. multiplier)
    description: str
    expiry: str = ""


@dataclass
class OrderStatus:
    filled: int
    avg_price: float
    done: bool


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f <= 0:
        return None
    return f


class Broker:
    """Interface. Alle metoder er blokerende."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def cash_usd(self) -> float: ...
    def index_price(self) -> Optional[float]: ...
    def opening_range(self, day: date, start: time, end: time) -> Optional[tuple[float, float]]: ...
    def chain(self, day: date) -> tuple[set[str], list[float]]: ...
    def option(self, expiry: str, strike: float, right: str) -> Any: ...
    def quote(self, contract: Any) -> Quote: ...
    def place_limit(self, contract: Any, action: str, qty: int, price: float) -> Any: ...
    def order_status(self, handle: Any) -> OrderStatus: ...
    def cancel(self, handle: Any) -> None: ...
    def option_positions(self) -> list[OptionPosition]: ...
    def cancel_open_orders(self) -> int: ...
    def describe(self, contract: Any) -> str: ...


class IBBroker(Broker):
    def __init__(self, cfg: Config, ib=None):
        if ib is None:
            from ib_async import IB

            ib = IB()
        self.cfg = cfg
        self.ib = ib
        self._index = None
        self._index_ticker = None
        self._tickers: dict[int, Any] = {}

    # -- forbindelse -------------------------------------------------------
    def connect(self) -> None:
        if self.ib.isConnected():
            return
        log.info("Forbinder til IB Gateway %s:%s (clientId=%s)", self.cfg.ib_host, self.cfg.ib_port, self.cfg.ib_client_id)
        self.ib.connect(
            self.cfg.ib_host,
            self.cfg.ib_port,
            clientId=self.cfg.ib_client_id,
            timeout=30,
            account=self.cfg.ib_account,
        )
        self.ib.reqMarketDataType(self.cfg.market_data_type)
        self._index = None
        self._index_ticker = None
        self._tickers = {}

    def disconnect(self) -> None:
        try:
            self.ib.disconnect()
        except Exception:
            pass

    def sleep(self, seconds: float) -> None:
        self.ib.sleep(seconds)

    # -- konto -------------------------------------------------------------
    def cash_usd(self) -> float:
        values = self.ib.accountValues(self.cfg.ib_account)
        by_tag: dict[str, float] = {}
        for v in values:
            if v.currency != "USD":
                continue
            try:
                by_tag[v.tag] = float(v.value)
            except (TypeError, ValueError):
                continue
        for tag in ("CashBalance", "TotalCashValue"):
            if tag in by_tag:
                cash = by_tag[tag]
                # På en cash-konto må vi ikke bruge mere end det disponible
                if "AvailableFunds" in by_tag:
                    cash = min(cash, by_tag["AvailableFunds"])
                return max(0.0, cash)
        raise RuntimeError("Kunne ikke finde USD-kontantbeholdning i kontoværdier")

    # -- indeks ------------------------------------------------------------
    def _index_contract(self):
        if self._index is None:
            from ib_async import Index

            c = Index(self.cfg.symbol, self.cfg.index_exchange, self.cfg.currency)
            q = self.ib.qualifyContracts(c)
            if not q or not getattr(q[0], "conId", 0):
                raise RuntimeError(f"Kunne ikke kvalificere indeks {self.cfg.symbol}")
            self._index = q[0]
        return self._index

    def index_price(self) -> Optional[float]:
        if self._index_ticker is None:
            self._index_ticker = self.ib.reqMktData(self._index_contract(), "", False, False)
            self.ib.sleep(2)
        t = self._index_ticker
        price = _num(t.last)
        if price is None:
            price = _num(t.marketPrice())
        return price

    def opening_range(self, day: date, start: time, end: time):
        bars = self.ib.reqHistoricalData(
            self._index_contract(),
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
        t0, t1 = at(day, start), at(day, end)
        highs, lows = [], []
        for b in bars or []:
            ts = b.date
            if isinstance(ts, datetime):
                ts = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=ET)
            else:
                continue
            if t0 <= ts < t1:
                highs.append(float(b.high))
                lows.append(float(b.low))
        if len(highs) < 25:
            log.warning("Kun %d 1-min bars i opening range (forventet 30)", len(highs))
        if not highs:
            return None
        return max(highs), min(lows)

    # -- optioner ----------------------------------------------------------
    def chain(self, day: date) -> tuple[set[str], list[float]]:
        idx = self._index_contract()
        params = self.ib.reqSecDefOptParams(idx.symbol, "", idx.secType, idx.conId)
        expirations: set[str] = set()
        for p in params or []:
            if p.tradingClass == self.cfg.trading_class:
                expirations.update(p.expirations)
        today = day.strftime("%Y%m%d")
        strikes: list[float] = []
        if today in expirations:
            from ib_async import Option

            probe = Option(
                self.cfg.symbol, today, exchange=self.cfg.option_exchange,
                currency=self.cfg.currency, tradingClass=self.cfg.trading_class,
            )
            details = self.ib.reqContractDetails(probe)
            strikes = sorted({float(d.contract.strike) for d in details})
        return expirations, strikes

    def option(self, expiry: str, strike: float, right: str):
        from ib_async import Option

        c = Option(
            self.cfg.symbol, expiry, strike, right, self.cfg.option_exchange,
            multiplier=str(self.cfg.multiplier), currency=self.cfg.currency,
            tradingClass=self.cfg.trading_class,
        )
        q = self.ib.qualifyContracts(c)
        if not q or not getattr(q[0], "conId", 0):
            raise RuntimeError(f"Kunne ikke kvalificere option {expiry} {strike} {right}")
        return q[0]

    def quote(self, contract) -> Quote:
        if not getattr(contract, "exchange", ""):
            contract.exchange = self.cfg.option_exchange
        t = self._tickers.get(contract.conId)
        if t is None:
            t = self.ib.reqMktData(contract, "", False, False)
            self._tickers[contract.conId] = t
            for _ in range(30):
                self.ib.sleep(0.1)
                if _num(t.ask) is not None:
                    break
        else:
            self.ib.sleep(0)
        return Quote(bid=_num(t.bid), ask=_num(t.ask))

    def place_limit(self, contract, action: str, qty: int, price: float):
        from ib_async import LimitOrder

        if not getattr(contract, "exchange", ""):
            contract.exchange = self.cfg.option_exchange
        order = LimitOrder(action, qty, price, tif="DAY")
        if self.cfg.ib_account:
            order.account = self.cfg.ib_account
        trade = self.ib.placeOrder(contract, order)
        log.info("Ordre sendt: %s %d %s @ %.2f (orderId=%s)", action, qty, self.describe(contract), price, order.orderId)
        return trade

    def order_status(self, trade) -> OrderStatus:
        self.ib.sleep(0)
        filled = int(round(trade.filled()))
        avg = float(trade.orderStatus.avgFillPrice or 0.0)
        if filled and not avg and trade.fills:
            tot = sum(f.execution.shares for f in trade.fills)
            avg = sum(f.execution.shares * f.execution.price for f in trade.fills) / tot if tot else 0.0
        return OrderStatus(filled=filled, avg_price=avg, done=trade.isDone())

    def cancel(self, trade) -> None:
        if trade.isDone():
            return
        self.ib.cancelOrder(trade.order)
        for _ in range(50):
            self.ib.sleep(0.1)
            if trade.isDone():
                return
        log.warning("Annullering ikke bekræftet for orderId=%s", trade.order.orderId)

    def option_positions(self) -> list[OptionPosition]:
        out = []
        for p in self.ib.positions(self.cfg.ib_account):
            c = p.contract
            if c.secType == "OPT" and c.symbol == self.cfg.symbol and p.position:
                if not c.exchange:
                    c.exchange = self.cfg.option_exchange
                out.append(
                    OptionPosition(
                        contract=c,
                        qty=int(p.position),
                        avg_cost=float(p.avgCost),
                        description=self.describe(c),
                        expiry=c.lastTradeDateOrContractMonth,
                    )
                )
        return out

    def cancel_open_orders(self) -> int:
        n = 0
        for trade in self.ib.reqAllOpenOrders():
            if trade.contract.symbol == self.cfg.symbol and not trade.isDone():
                log.warning("Annullerer efterladt ordre: %s", trade.order)
                self.ib.cancelOrder(trade.order)
                n += 1
        if n:
            self.ib.sleep(2)
        return n

    def describe(self, c) -> str:
        local = getattr(c, "localSymbol", "")
        if local:
            return local.strip()
        return f"{c.symbol} {c.lastTradeDateOrContractMonth} {c.strike:g}{c.right}"

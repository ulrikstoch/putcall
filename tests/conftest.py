"""Fælles test-fixtures: falsk ur, falsk broker og konfiguration."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xspbot.broker import Broker, OptionPosition, OrderStatus  # noqa: E402
from xspbot.calendar import ET  # noqa: E402
from xspbot.config import Config  # noqa: E402
from xspbot.engine import Bot  # noqa: E402
from xspbot.pricing import Quote  # noqa: E402
from xspbot.state import StateStore  # noqa: E402

TRADING_DAY = date(2026, 11, 10)  # en almindelig tirsdag


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@dataclass
class FakeContract:
    symbol: str
    expiry: str
    strike: float
    right: str
    conId: int = 0
    localSymbol: str = ""


@dataclass
class FakeOrder:
    action: str
    qty: int
    price: float
    placed_at: datetime
    fill_after: Optional[float]  # sekunder; None = fyldes aldrig
    filled: int = 0
    cancelled: bool = False
    contract: Any = None

    def done(self, now: datetime) -> bool:
        return self.cancelled or self.filled >= self.qty


class FakeBroker(Broker):
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.cash = 150.0
        self.strikes = [float(k) for k in range(650, 711)]
        self.expirations = {TRADING_DAY.strftime("%Y%m%d")}
        self.range = (680.0, 678.0)
        self.index_fn: Callable[[datetime], Optional[float]] = lambda t: 679.0
        self.quote_fn: Callable[[Any, datetime], Quote] = lambda c, t: Quote(1.00, 1.05)
        self.fill_after: Callable[[str, int], Optional[float]] = lambda action, n: 0.0
        self.orders: list[FakeOrder] = []
        self.positions: list[OptionPosition] = []
        self.connected = False
        self.cancel_open_calls = 0
        self.on_sleep: Optional[Callable[[], None]] = None

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def sleep(self, seconds):
        self.clock.advance(seconds if seconds > 0 else 0.001)
        self._process()
        if self.on_sleep:
            self.on_sleep()

    def _process(self):
        now = self.clock.now()
        for o in self.orders:
            if o.cancelled or o.filled >= o.qty or o.fill_after is None:
                continue
            if (now - o.placed_at).total_seconds() >= o.fill_after:
                o.filled = o.qty
                self._book(o)

    def _book(self, o):
        sign = 1 if o.action == "BUY" else -1
        for i, p in enumerate(self.positions):
            if p.contract.conId == o.contract.conId:
                q = p.qty + sign * o.filled
                if q == 0:
                    self.positions.pop(i)
                else:
                    self.positions[i] = OptionPosition(p.contract, q, p.avg_cost, p.description, p.expiry)
                return
        if sign > 0:
            self.positions.append(
                OptionPosition(o.contract, o.filled, o.price * 100, self.describe(o.contract), o.contract.expiry)
            )

    def cash_usd(self):
        return self.cash

    def index_price(self):
        return self.index_fn(self.clock.now())

    def opening_range(self, day, start, end):
        return self.range

    def chain(self, day):
        return self.expirations, self.strikes

    def option(self, expiry, strike, right):
        return FakeContract("XSP", expiry, strike, right, conId=int(strike * 10),
                            localSymbol=f"XSP {expiry} {strike:g}{right}")

    def quote(self, contract):
        return self.quote_fn(contract, self.clock.now())

    def place_limit(self, contract, action, qty, price):
        n = sum(1 for o in self.orders if o.action == action)
        o = FakeOrder(action, qty, price, self.clock.now(), self.fill_after(action, n), contract=contract)
        self.orders.append(o)
        self._process()
        return o

    def order_status(self, o):
        self._process()
        return OrderStatus(o.filled, o.price if o.filled else 0.0, o.done(self.clock.now()))

    def cancel(self, o):
        o.cancelled = True

    def option_positions(self):
        return list(self.positions)

    def cancel_open_orders(self):
        self.cancel_open_calls += 1
        return 0

    def describe(self, c):
        return c.localSymbol or f"XSP {c.expiry} {c.strike:g}{c.right}"


class RecordingNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text, level=None):
        self.messages.append(text)

    def error(self, text):
        self.messages.append("ERROR " + text)

    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def cfg(tmp_path):
    return Config(project_dir=tmp_path, poll_interval_sec=1.0)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 10, 9, 15, tzinfo=ET))


@pytest.fixture
def broker(clock):
    return FakeBroker(clock)


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def store(cfg):
    return StateStore(cfg.state_path)


@pytest.fixture
def make_bot(cfg, broker, notifier, store, clock):
    def _make(**overrides):
        c = cfg
        if overrides:
            import dataclasses

            c = dataclasses.replace(cfg, **overrides)
        return Bot(c, broker, notifier, store, now=clock.now)

    return _make

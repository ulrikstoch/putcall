"""Rene beregningsfunktioner: tick-afrunding, strike-valg, størrelse, filtre og exit."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Iterable, Optional


def tick_size(price: float) -> float:
    """XSP: $0.01 under $3, ellers $0.05."""
    return 0.01 if price < 3.0 else 0.05


def round_to_tick(price: float, direction: str = "nearest") -> float:
    """Afrund til gyldig tick. direction: 'up' (køb), 'down' (salg) eller 'nearest'."""
    if price is None or price <= 0 or math.isnan(price):
        raise ValueError(f"Ugyldig pris: {price!r}")
    mode = {"up": ROUND_CEILING, "down": ROUND_FLOOR, "nearest": ROUND_HALF_UP}[direction]
    # Rens float-støj (fx 2.9999999997) før afrunding
    p = Decimal(str(round(price, 6)))
    tick = Decimal(str(tick_size(float(p))))
    rounded = (p / tick).to_integral_value(rounding=mode) * tick
    # Krydser vi $3-grænsen ved oprunding, skal resultatet stadig være gyldigt
    if rounded >= 3 and tick == Decimal("0.01"):
        tick = Decimal("0.05")
        rounded = (p / tick).to_integral_value(rounding=mode) * tick
    if rounded <= 0:
        rounded = Decimal("0.01")
    return float(rounded)


def pick_strike(strikes: Iterable[float], price: float, right: str) -> Optional[float]:
    """CALL: første strike strengt over kursen. PUT: første strike strengt under."""
    s = sorted(set(float(x) for x in strikes))
    if right == "C":
        above = [k for k in s if k > price]
        return above[0] if above else None
    if right == "P":
        below = [k for k in s if k < price]
        return below[-1] if below else None
    raise ValueError(f"Ukendt right: {right!r}")


def contract_cost(ask: float, fee: float, multiplier: int = 100) -> float:
    return ask * multiplier + fee


def position_size(cash: float, ask: float, fee: float, multiplier: int = 100) -> int:
    """Alt-ind: floor(cash / (ask*100 + gebyr))."""
    if cash <= 0 or ask <= 0:
        return 0
    return max(0, math.floor(cash / contract_cost(ask, fee, multiplier) + 1e-9))


@dataclass
class Quote:
    bid: Optional[float]
    ask: Optional[float]

    @staticmethod
    def _valid(x) -> bool:
        return x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0

    @property
    def has_bid(self) -> bool:
        return self._valid(self.bid)

    @property
    def has_ask(self) -> bool:
        return self._valid(self.ask)

    @property
    def mid(self) -> Optional[float]:
        if not self.has_ask:
            return None
        bid = self.bid if self.has_bid else 0.0
        return (bid + self.ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if not (self.has_ask and self.has_bid):
            return None
        return self.ask - self.bid


def entry_filter(q: Quote, min_mid: float, max_spread_pct: float) -> Optional[str]:
    """Returnér None hvis OK, ellers en begrundelse for at springe over."""
    if not q.has_ask or not q.has_bid:
        return f"ingen gyldig bid/ask (bid={q.bid}, ask={q.ask})"
    mid = q.mid
    if mid < min_mid - 1e-9:
        return f"mid {mid:.3f} < {min_mid:.2f}"
    if q.spread > max_spread_pct * mid + 1e-9:
        return f"spread {q.spread:.3f} > {max_spread_pct:.0%} af mid {mid:.3f}"
    return None


def breakout_signal(price: float, high: float, low: float, buffer: float) -> Optional[str]:
    if price is None:
        return None
    if price > high + buffer:
        return "C"
    if price < low - buffer:
        return "P"
    return None


def exit_reason(
    mid: Optional[float],
    entry: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    time_up: bool,
    stop_file: bool = False,
) -> Optional[str]:
    if stop_file:
        return "nødstop"
    if mid is not None and entry > 0:
        if mid >= entry * (1 + take_profit_pct) - 1e-9:
            return "take profit"
        if mid <= entry * (1 - stop_loss_pct) + 1e-9:
            return "stop loss"
    if time_up:
        return "tidsstop"
    return None


def pnl_usd(entry: float, exit_price: float, qty: int, fee: float, multiplier: int = 100) -> float:
    """P/L inkl. estimeret gebyr på både køb og salg."""
    return (exit_price - entry) * multiplier * qty - 2 * fee * qty

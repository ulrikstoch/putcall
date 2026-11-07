"""Daglig backtest-motor med realistiske omkostninger for en lille IBKR-konto.

Timing: vægte fra lukningen på dag t handles på åbningen dag t+1 (+ slippage).
Omkostninger: IBKR Pro tiered for aktier/ETF'er: $0.0035 pr. aktie, min $0.35,
max 1 % af handelsværdien pr. ordre. Brøkdele af aktier er tilladt som standard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import Prices
from .strategies import Strategy


@dataclass
class Costs:
    per_share: float = 0.0035
    min_commission: float = 0.35
    max_pct: float = 0.01
    slippage_bps: float = 5.0
    fractional: bool = True
    band: float = 0.05  # handl kun hvis vægten afviger mere end 5 %-point

    def commission(self, shares: float, price: float) -> float:
        value = abs(shares) * price
        if value <= 0:
            return 0.0
        return min(max(self.min_commission, self.per_share * abs(shares)), self.max_pct * value)


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    weights: pd.DataFrame
    costs_paid: float
    meta: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()


def target_weights(strategy: type[Strategy], params: dict, close: pd.DataFrame) -> pd.DataFrame:
    w = strategy.weights(close, params).reindex(index=close.index, columns=close.columns).fillna(0.0)
    w = w.clip(lower=0.0)
    s = w.sum(axis=1)
    over = s > 1.0 + 1e-9
    w.loc[over] = w.loc[over].div(s[over], axis=0)
    return w


def run(
    strategy: type[Strategy],
    params: dict,
    prices: Prices,
    start=None,
    end=None,
    capital: float = 150.0,
    costs: Costs | None = None,
) -> Result:
    costs = costs or Costs()
    syms = strategy.universe(params)
    close = prices.close[syms]
    opn = prices.open[syms]
    if end is not None:
        close, opn = close.loc[: pd.Timestamp(end)], opn.loc[: pd.Timestamp(end)]
    w = target_weights(strategy, params, close)

    idx = close.index
    i0 = 1
    if start is not None:
        i0 = max(1, int(idx.searchsorted(pd.Timestamp(start))))
    cash = capital
    shares = {s: 0.0 for s in syms}
    eq_vals, eq_idx, trades = [], [], []
    paid = 0.0
    c_arr, o_arr, w_arr = close.to_numpy(), opn.to_numpy(), w.to_numpy()
    slip = costs.slippage_bps / 1e4

    # startværdi registreres dagen før første handelsdag
    eq_idx.append(idx[i0 - 1])
    eq_vals.append(capital)
    for i in range(i0, len(idx)):
        o = o_arr[i]
        target = w_arr[i - 1]
        # værdi ved åbning
        val = cash + sum(shares[s] * (o[j] if not math.isnan(o[j]) else 0.0) for j, s in enumerate(syms))
        if val > 0:
            cur_w = {s: (shares[s] * o[j] / val if not math.isnan(o[j]) else 0.0) for j, s in enumerate(syms)}
            orders = []
            for j, s in enumerate(syms):
                tw = 0.0 if math.isnan(o[j]) else float(target[j])
                cw = cur_w[s]
                change = abs(tw - cw) > costs.band or (tw == 0 and shares[s] > 0) or (tw > 0 and cw == 0)
                if not change or math.isnan(o[j]):
                    continue
                tgt_sh = tw * val / o[j]
                if not costs.fractional:
                    tgt_sh = math.floor(tgt_sh)
                delta = tgt_sh - shares[s]
                if abs(delta) * o[j] < 1.0:  # under $1: ignorér
                    continue
                orders.append((j, s, delta))
            # salg først, så køb
            for j, s, delta in sorted(orders, key=lambda x: x[2]):
                px = o[j] * (1 + slip if delta > 0 else 1 - slip)
                if delta > 0:
                    comm = costs.commission(delta, px)
                    affordable = max(0.0, (cash - comm) / px)
                    if affordable < delta:
                        delta = affordable if costs.fractional else math.floor(affordable)
                        comm = costs.commission(delta, px)
                    if delta * px < 1.0:
                        continue
                    cash -= delta * px + comm
                else:
                    comm = costs.commission(delta, px)
                    cash += -delta * px - comm
                shares[s] += delta
                paid += comm
                trades.append((idx[i], s, delta, px, comm))
        c = c_arr[i]
        v = cash + sum(shares[s] * (c[j] if not math.isnan(c[j]) else 0.0) for j, s in enumerate(syms))
        eq_idx.append(idx[i])
        eq_vals.append(v)

    equity = pd.Series(eq_vals, index=pd.DatetimeIndex(eq_idx), name="equity")
    tdf = pd.DataFrame(trades, columns=["date", "symbol", "shares", "price", "commission"])
    return Result(equity, tdf, w, paid, {"strategy": strategy.key(), "params": params, "capital": capital})


def lookahead_check(strategy: type[Strategy], params: dict, prices: Prices, n_checks: int = 6) -> list[str]:
    """Beregn vægte på afkortede data og sammenlign med vægte på fulde data.

    Afviger de på samme dato, bruger strategien fremtidige data (look-ahead).
    """
    syms = strategy.universe(params)
    close = prices.close[syms].dropna(how="all")
    full = target_weights(strategy, params, close)
    problems = []
    n = len(close)
    for cut in np.linspace(n * 0.3, n - 2, n_checks).astype(int):
        part = target_weights(strategy, params, close.iloc[: cut + 1])
        a = part.iloc[-1].to_numpy()
        b = full.iloc[cut].to_numpy()
        if not np.allclose(a, b, atol=1e-9, equal_nan=True):
            problems.append(f"{close.index[cut].date()}: vægte ændrer sig når fremtidige data tilføjes")
    return problems

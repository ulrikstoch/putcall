"""Indikatorer. Alle bruger kun data til og med dag t (ingen look-ahead).

Denne fil indgår i strategiernes hash: ændres den, nulstilles inkubationen
for ALLE kandidater.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close.rolling(n, min_periods=n).mean()


def total_return(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close / close.shift(n) - 1.0


def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder-RSI."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def month_start_mask(index: pd.DatetimeIndex) -> pd.Series:
    """Sand på første handelsdag i hver måned. Kendt samme dag (ser kun bagud)."""
    s = pd.Series(index.month, index=index)
    return s != s.shift(1)


def hold_between(signal_rows: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """Behold vægte fra seneste rebalanceringsdag (forward fill)."""
    w = signal_rows.where(mask, np.nan)
    return w.ffill().fillna(0.0)

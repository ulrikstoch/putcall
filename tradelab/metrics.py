"""Nøgletal, Probabilistic Sharpe Ratio og Deflated Sharpe Ratio.

Deflated Sharpe Ratio (Bailey & López de Prado, 2014) korrigerer for, at man har
prøvet mange varianter: jo flere forsøg, jo højere skal Sharpe være, før den
tæller som reel og ikke held.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

N = NormalDist()
EULER_GAMMA = 0.5772156649015329
PERIODS = 252


def sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=1) < 1e-12:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(PERIODS))


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min()) if len(equity) else 0.0


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def psr(returns: pd.Series, sr_benchmark_ann: float = 0.0) -> float:
    """Sandsynlighed for at den sande Sharpe > benchmark (alle i årlige enheder ind/ud)."""
    r = returns.dropna()
    t = len(r)
    if t < 10 or r.std(ddof=1) < 1e-12:
        return 0.0
    sr = r.mean() / r.std(ddof=1)  # pr. periode
    sr_b = sr_benchmark_ann / math.sqrt(PERIODS)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0  # ikke-excess
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.0
    z = (sr - sr_b) * math.sqrt(t - 1) / math.sqrt(denom)
    return float(N.cdf(z))


def expected_max_sharpe(n_trials: int, var_sr_ann: float) -> float:
    """Forventet maksimal Sharpe (årlig) blandt n_trials forsøg uden ægte edge."""
    if n_trials <= 1:
        return 0.0
    sd = math.sqrt(max(var_sr_ann, 0.0))
    a = N.inv_cdf(1 - 1 / n_trials)
    b = N.inv_cdf(1 - 1 / (n_trials * math.e))
    return sd * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(returns: pd.Series, n_trials: int, trial_sharpes_ann: list[float]) -> float:
    if len(trial_sharpes_ann) >= 5:
        var = float(np.var(trial_sharpes_ann, ddof=1))
    else:
        # for få forsøg til at estimere variansen: brug Sharpe-estimatets egen varians
        years = max(len(returns) / PERIODS, 1.0)
        var = 1.0 / years
    return psr(returns, expected_max_sharpe(max(n_trials, 1), var))


def subperiod_returns(equity: pd.Series, parts: int = 4) -> list[float]:
    chunks = np.array_split(np.arange(len(equity)), parts)
    out = []
    for c in chunks:
        if len(c) < 2:
            continue
        e = equity.iloc[c]
        out.append(float(e.iloc[-1] / e.iloc[0] - 1))
    return out


def summary(equity: pd.Series, n_trades: int = 0) -> dict:
    r = equity.pct_change().dropna()
    return {
        "start": str(equity.index[0].date()) if len(equity) else None,
        "end": str(equity.index[-1].date()) if len(equity) else None,
        "days": int(len(r)),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) > 1 else 0.0,
        "cagr": cagr(equity),
        "sharpe": sharpe(r),
        "max_dd": max_drawdown(equity),
        "trades": int(n_trades),
        "final": float(equity.iloc[-1]) if len(equity) else 0.0,
    }

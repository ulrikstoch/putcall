"""Daglige kurser (justeret for udbytte/splits) fra Yahoo Finance med lokal cache."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data"


@dataclass
class Prices:
    open: pd.DataFrame  # justeret åbningskurs
    close: pd.DataFrame  # justeret lukkekurs

    def upto(self, last_date) -> "Prices":
        last_date = pd.Timestamp(last_date)
        return Prices(self.open.loc[:last_date], self.close.loc[:last_date])


def _download(symbols: list[str], start: str) -> Prices:
    import yfinance as yf

    raw = yf.download(sorted(symbols), start=start, auto_adjust=False, progress=False,
                      group_by="column", threads=True)
    if raw is None or raw.empty:
        raise RuntimeError("Ingen data fra Yahoo")
    close = raw["Close"]
    adj = raw["Adj Close"]
    factor = adj / close
    op = raw["Open"] * factor
    if isinstance(adj, pd.Series):  # kun ét symbol
        adj = adj.to_frame(symbols[0])
        op = op.to_frame(symbols[0])
    adj.index = pd.DatetimeIndex(adj.index).tz_localize(None).normalize()
    op.index = adj.index
    adj = adj.dropna(how="all")
    op = op.loc[adj.index]
    return Prices(op[sorted(symbols)], adj[sorted(symbols)])


def load_prices(symbols, start: str = "2000-01-01", max_age_hours: float = 6.0, cache_dir: Path = CACHE_DIR) -> Prices:
    symbols = sorted(set(symbols))
    key = hashlib.sha1(f"{','.join(symbols)}|{start}".encode()).hexdigest()[:12]
    cache_dir.mkdir(parents=True, exist_ok=True)
    fo, fc = cache_dir / f"daily_{key}_open.csv", cache_dir / f"daily_{key}_close.csv"
    if fc.exists() and time.time() - fc.stat().st_mtime < max_age_hours * 3600:
        o = pd.read_csv(fo, index_col=0, parse_dates=[0])
        c = pd.read_csv(fc, index_col=0, parse_dates=[0])
        return Prices(o, c)
    p = _download(symbols, start)
    p.open.to_csv(fo)
    p.close.to_csv(fc)
    return p


def completed_sessions_only(p: Prices, today) -> Prices:
    """Fjern dagens (ufærdige) bar, så signaler kun bygger på afsluttede dage."""
    today = pd.Timestamp(today).normalize()
    return Prices(p.open.loc[p.open.index < today], p.close.loc[p.close.index < today])

"""Strategibibliotek.

REGLER (håndhæves af hash og tests):
- En strategi returnerer en DataFrame med målvægte (0..1, rækkesum <= 1) med samme
  index som ``close``. Vægten på dag t må KUN bruge data til og med t. Motoren
  handler vægten fra lukningen på dag t ved åbningen på dag t+1.
- En strategi, der er registreret som kandidat, må ALDRIG ændres. Lav en ny klasse
  med højere version (fx ``TrendSMA`` v2) i stedet. Hashen af klassens kildekode +
  indicators.py + parametre identificerer kandidaten; ændres noget, er det en ny
  kandidat med ny inkubation.
- Ikke-investeret andel står som kontant (0 % rente). Brug ``cash`` (fx BIL) for
  at tjene korte renter.
"""

from __future__ import annotations

import inspect
import numpy as np
import pandas as pd

from . import indicators as ind


class Strategy:
    name: str = ""
    version: int = 1
    description: str = ""
    defaults: dict = {}

    @classmethod
    def key(cls) -> str:
        return f"{cls.name}_v{cls.version}"

    @classmethod
    def params(cls, overrides: dict | None = None) -> dict:
        p = dict(cls.defaults)
        for k, v in (overrides or {}).items():
            if k not in p:
                raise KeyError(f"{cls.key()}: ukendt parameter {k!r} (gyldige: {sorted(p)})")
            p[k] = v
        return p

    @classmethod
    def universe(cls, params: dict) -> list[str]:
        raise NotImplementedError

    @classmethod
    def weights(cls, close: pd.DataFrame, params: dict) -> pd.DataFrame:
        raise NotImplementedError

    @classmethod
    def source(cls) -> str:
        return inspect.getsource(cls)


def _frame(close: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(0.0, index=close.index, columns=close.columns)


class BuyHold(Strategy):
    name = "BuyHold"
    version = 1
    description = "Benchmark: 100 % i ét symbol hele tiden."
    defaults = {"symbol": "SPY"}

    @classmethod
    def universe(cls, p):
        return [p["symbol"]]

    @classmethod
    def weights(cls, close, p):
        w = _frame(close)
        w[p["symbol"]] = close[p["symbol"]].notna().astype(float)
        return w


class TrendSMA(Strategy):
    name = "TrendSMA"
    version = 1
    description = "Hold symbol når lukkekurs > SMA(n), ellers kontant-ETF."
    defaults = {"symbol": "SPY", "n": 200, "cash": "BIL"}

    @classmethod
    def universe(cls, p):
        return [p["symbol"], p["cash"]]

    @classmethod
    def weights(cls, close, p):
        w = _frame(close)
        px = close[p["symbol"]]
        on = (px > ind.sma(close[[p["symbol"]]], p["n"])[p["symbol"]]).astype(float)
        w[p["symbol"]] = on
        w[p["cash"]] = (1 - on) * close[p["cash"]].notna()
        return w


class DualMomentum(Strategy):
    name = "DualMomentum"
    version = 1
    description = (
        "Månedlig rotation: vælg de 'top' risiko-ETF'er med højest afkast over 'lookback' dage, "
        "men kun hvis afkastet slår kontant-ETF'en (absolut momentum). Ellers kontant."
    )
    defaults = {
        "assets": ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD"],
        "cash": "BIL",
        "lookback": 252,
        "top": 1,
    }

    @classmethod
    def universe(cls, p):
        return list(p["assets"]) + [p["cash"]]

    @classmethod
    def weights(cls, close, p):
        assets, cash = list(p["assets"]), p["cash"]
        mom = ind.total_return(close[assets], p["lookback"])
        cash_mom = ind.total_return(close[[cash]], p["lookback"])[cash].fillna(0.0)
        raw = _frame(close)
        ranks = mom.rank(axis=1, ascending=False, method="first")
        chosen = (ranks <= p["top"]) & mom.gt(cash_mom, axis=0)
        per = 1.0 / p["top"]
        raw[assets] = chosen.astype(float) * per
        has_any = mom.notna().any(axis=1)
        raw[cash] = (1.0 - raw[assets].sum(axis=1)) * close[cash].notna() * has_any
        raw.loc[~has_any] = 0.0
        return ind.hold_between(raw, ind.month_start_mask(close.index))


class RSI2MeanReversion(Strategy):
    name = "RSI2MeanReversion"
    version = 1
    description = (
        "Køb symbol når RSI(rsi_n) < entry og kurs > SMA(trend); sælg når kurs > SMA(exit_n). "
        "Ellers kontant-ETF."
    )
    defaults = {"symbol": "SPY", "rsi_n": 2, "entry": 10, "trend": 200, "exit_n": 5, "cash": "BIL"}

    @classmethod
    def universe(cls, p):
        return [p["symbol"], p["cash"]]

    @classmethod
    def weights(cls, close, p):
        s = p["symbol"]
        px = close[s]
        r = ind.rsi(close[[s]], p["rsi_n"])[s]
        trend = ind.sma(close[[s]], p["trend"])[s]
        exit_ma = ind.sma(close[[s]], p["exit_n"])[s]
        enter = (r < p["entry"]) & (px > trend)
        leave = px > exit_ma
        state = np.zeros(len(px))
        held = False
        for i, (e, l) in enumerate(zip(enter.to_numpy(), leave.to_numpy())):
            if held and l:
                held = False
            elif not held and e:
                held = True
            state[i] = 1.0 if held else 0.0
        w = _frame(close)
        w[s] = state
        w[p["cash"]] = (1 - state) * close[p["cash"]].notna()
        return w


# ---------------------------------------------------------------------------
STRATEGIES: dict[str, type[Strategy]] = {}


def register_strategy(cls: type[Strategy]) -> type[Strategy]:
    k = cls.key()
    if k in STRATEGIES and STRATEGIES[k] is not cls:
        raise ValueError(f"Strategi {k} findes allerede - brug en ny version")
    STRATEGIES[k] = cls
    return cls


for _c in (BuyHold, TrendSMA, DualMomentum, RSI2MeanReversion):
    register_strategy(_c)

# Agenters nye strategier ligger i tradelab/strategies_lab.py og registreres dér.
try:  # pragma: no cover - afhænger af agenternes fil
    from . import strategies_lab  # noqa: F401
except ImportError:
    pass


def get(key: str) -> type[Strategy]:
    try:
        return STRATEGIES[key]
    except KeyError:
        raise KeyError(f"Ukendt strategi {key!r}. Kendte: {sorted(STRATEGIES)}") from None

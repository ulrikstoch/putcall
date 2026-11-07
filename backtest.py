#!/usr/bin/env python3
"""Backtest af XSP 0DTE opening range breakout med samme regler som botten.

Datakilder for indekset (1-min bars):
  --source yahoo  (standard) ^GSPC/10 fra Yahoo. Kun ca. 30 dage 1-min
                  (ældre dage suppleres med 2-min bars, max ca. 60 dage).
  --source ib     Henter 1-min XSP-bars og VIX fra IB Gateway (års historik).
                  Kræver at IB Gateway kører. Data caches i data/.
  --source csv    --index-csv fil med kolonnerne datetime,open,high,low,close
                  (+ evt. --vix-csv med datetime,close). Tider i ET eller med tz.

Optionspriser: Gratis historiske 0DTE-optionspriser findes ikke, og IB leverer
ikke historik for udløbne optioner. Optionspriser ESTIMERES derfor med
Black-Scholes med VIX som volatilitet plus en modelleret bid/ask-spread.
Det står tydeligt i rapporten.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xspbot.calendar import ET, is_half_day  # noqa: E402
from xspbot.config import Config  # noqa: E402
from xspbot.pricing import (  # noqa: E402
    breakout_signal,
    entry_filter,
    pick_strike,
    position_size,
    round_to_tick,
    Quote,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
MIN_PER_YEAR = 252 * 390  # handelsminutter pr. år (intradag-varians)


# ---------------------------------------------------------------------------
# Black-Scholes
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, minutes_left: float, sigma: float, right: str, r: float = 0.04) -> float:
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    if minutes_left <= 0 or sigma <= 0:
        return intrinsic
    t = minutes_left / MIN_PER_YEAR
    sq = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / sq
    d2 = d1 - sq
    disc = math.exp(-r * t)
    if right == "C":
        return spot * _ncdf(d1) - strike * disc * _ncdf(d2)
    return strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)


@dataclass
class Model:
    spread_pct: float = 0.06  # samlet spread som andel af mid
    min_spread: float = 0.02
    vol_mult: float = 1.0
    rate: float = 0.04

    def quote(self, mid: float) -> Quote:
        spread = max(self.min_spread, self.spread_pct * mid)
        bid = mid - spread / 2
        ask = mid + spread / 2
        return Quote(round_to_tick(bid, "down") if bid > 0.005 else None, round_to_tick(max(ask, 0.01), "up"))


# ---------------------------------------------------------------------------
# Data
def _to_et(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        return idx.tz_localize(ET)
    return idx.tz_convert(ET)


def load_yahoo(days_cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    import yfinance as yf

    def fetch(sym, interval, chunks=None, period=None):
        frames = []
        if chunks:
            end = datetime.now()
            for i in range(chunks):
                e = end - timedelta(days=7 * i)
                s = e - timedelta(days=7)
                try:
                    frames.append(yf.Ticker(sym).history(start=s, end=e, interval=interval))
                except Exception:
                    pass
        else:
            frames.append(yf.Ticker(sym).history(period=period, interval=interval))
        df = pd.concat([f for f in frames if len(f)]).sort_index()
        df = df[~df.index.duplicated()]
        df.index = _to_et(df.index)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close"]]
        return df

    spx1 = fetch("^GSPC", "1m", chunks=5)
    spx2 = fetch("^GSPC", "2m", period="60d")
    vix1 = fetch("^VIX", "1m", chunks=5)
    vix2 = fetch("^VIX", "2m", period="60d")
    vixd = yf.Ticker("^VIX").history(period="1y", interval="1d")
    vixd.index = _to_et(vixd.index)

    # Brug 1-min hvor det findes, ellers 2-min
    days1 = set(spx1.index.date)
    spx2 = spx2[[d not in days1 for d in spx2.index.date]]
    spx2 = spx2.assign(barmin=2)
    spx1 = spx1.assign(barmin=1)
    idx = pd.concat([spx1, spx2]).sort_index()
    idx[["open", "high", "low", "close"]] /= 10.0  # SPX -> XSP
    vix = pd.concat([vix1, vix2[[d not in set(vix1.index.date) for d in vix2.index.date]]]).sort_index()
    vix_daily = vixd.rename(columns=str.lower)[["open"]]
    note = (
        f"Yahoo Finance: ^GSPC/10 som XSP ({len(days1)} dage med 1-min bars, "
        f"{len(set(spx2.index.date))} ældre dage med 2-min bars), ^VIX intradag."
    )
    days_cache.mkdir(exist_ok=True)
    idx.to_csv(days_cache / "yahoo_xsp.csv")
    vix.to_csv(days_cache / "yahoo_vix.csv")
    return idx, (vix, vix_daily), note


def load_csv(index_csv: str, vix_csv: str | None):
    idx = pd.read_csv(index_csv, parse_dates=[0], index_col=0)
    idx.index = _to_et(idx.index)
    idx = idx.rename(columns=str.lower)
    if "barmin" not in idx:
        idx["barmin"] = 1
    vix = None
    if vix_csv:
        vix = pd.read_csv(vix_csv, parse_dates=[0], index_col=0)
        vix.index = _to_et(vix.index)
        vix = vix.rename(columns=str.lower)
    return idx, (vix, None), f"CSV: {index_csv}" + (f", VIX: {vix_csv}" if vix_csv else " (fast VIX)")


def load_ib(n_days: int, symbol: str = "XSP"):
    """Henter 1-min bars dag for dag fra IB Gateway og cacher dem i data/."""
    from ib_async import IB, Index

    cfg = Config.from_env()
    DATA_DIR.mkdir(exist_ok=True)
    ib = IB()
    ib.connect(cfg.ib_host, cfg.ib_port, clientId=cfg.ib_client_id + 50, timeout=30)
    out = {}
    for sym in (symbol, "VIX"):
        c = ib.qualifyContracts(Index(sym, "CBOE", "USD"))[0]
        frames = []
        day = datetime.now(ET).date()
        got = 0
        tries = 0
        while got < n_days and tries < n_days * 2:
            tries += 1
            day -= timedelta(days=1)
            if day.weekday() >= 5:
                continue
            cache = DATA_DIR / f"ib_{sym}_{day:%Y%m%d}.csv"
            if cache.exists():
                df = pd.read_csv(cache, parse_dates=[0], index_col=0)
            else:
                end = datetime.combine(day, time(16, 0), tzinfo=ET).astimezone(tz=None)
                bars = ib.reqHistoricalData(c, endDateTime=end, durationStr="1 D", barSizeSetting="1 min",
                                            whatToShow="TRADES", useRTH=True, formatDate=2)
                if not bars:
                    continue
                df = pd.DataFrame([{"datetime": b.date, "open": b.open, "high": b.high, "low": b.low,
                                    "close": b.close} for b in bars]).set_index("datetime")
                df.to_csv(cache)
                ib.sleep(1)
            df.index = _to_et(df.index)
            if len(df):
                frames.append(df)
                got += 1
        out[sym] = pd.concat(frames).sort_index()
    ib.disconnect()
    idx = out[symbol].assign(barmin=1)
    return idx, (out["VIX"], None), f"IB Gateway: {symbol} og VIX 1-min bars, {idx.index.normalize().nunique()} dage"


# ---------------------------------------------------------------------------
# Simulation af én dag
@dataclass
class Trade:
    day: str
    right: str
    signal_time: str
    spot: float
    strike: float
    vix: float
    entry: float  # ask (pr. aktie)
    exit: float  # bid
    reason: str
    exit_time: str
    pl_1: float  # P/L for 1 kontrakt inkl. gebyrer
    ret: float  # (exit-entry)/entry


def vix_at(vix_data, ts: pd.Timestamp, fixed: float | None) -> float:
    if fixed:
        return fixed
    intraday, daily = vix_data
    if intraday is not None and len(intraday):
        s = intraday.loc[:ts]
        if len(s) and s.index[-1].date() == ts.date():
            return float(s["close"].iloc[-1])
    if daily is not None and len(daily):
        s = daily.loc[: ts.normalize() + pd.Timedelta(hours=23)]
        if len(s):
            return float(s["open"].iloc[-1])
    return 18.0


def minutes_to_close(ts: pd.Timestamp) -> float:
    close = ts.normalize() + pd.Timedelta(hours=16)
    return max(0.0, (close - ts).total_seconds() / 60)


def simulate_day(day_bars: pd.DataFrame, vix_data, cfg: Config, model: Model, fixed_vix=None):
    d = day_bars.index[0].date()
    barmin = int(day_bars["barmin"].iloc[0]) if "barmin" in day_bars else 1
    t = day_bars.index.time
    rng = day_bars[(t >= cfg.range_start) & (t < cfg.range_end)]
    if len(rng) < (30 // barmin) * 0.8:
        return None, "manglende opening-range-data"
    high, low = float(rng["high"].max()), float(rng["low"].min())
    up, dn = high + cfg.breakout_buffer, low - cfg.breakout_buffer

    scan = day_bars[(t >= cfg.range_end) & (t < cfg.entry_deadline)]
    sig = None
    for ts, b in scan.iterrows():
        hit_up = b["high"] > up
        hit_dn = b["low"] < dn
        if hit_up and hit_dn:
            right = "C" if abs(b["open"] - up) < abs(b["open"] - dn) else "P"
        elif hit_up:
            right = "C"
        elif hit_dn:
            right = "P"
        else:
            continue
        # Kursen på det sekund, hvor niveauet brydes (eller åbningen ved gap)
        spot = max(b["open"], up + 0.01) if right == "C" else min(b["open"], dn - 0.01)
        assert breakout_signal(spot, high, low, cfg.breakout_buffer) == right
        sig = (ts, right, float(spot))
        break
    if sig is None:
        return None, "intet signal"

    ts0, right, spot = sig
    strikes = np.arange(math.floor(spot) - 20, math.ceil(spot) + 21, 1.0)
    strike = pick_strike(strikes, spot, right)
    vix = vix_at(vix_data, ts0, fixed_vix)
    sigma = vix / 100 * model.vol_mult
    mid0 = bs_price(spot, strike, minutes_to_close(ts0), sigma, right, model.rate)
    q0 = model.quote(mid0)
    why = entry_filter(q0, cfg.min_mid, cfg.max_spread_pct)
    if why:
        return None, f"filter: {why}"
    entry = q0.ask

    tp_mid = entry * (1 + cfg.take_profit_pct)
    sl_mid = entry * (1 - cfg.stop_loss_pct)
    exit_px, reason, exit_ts = None, None, None
    after = day_bars[day_bars.index > ts0]
    for ts, b in after.iterrows():
        if ts.time() >= cfg.time_stop:
            m = bs_price(b["open"], strike, minutes_to_close(ts), sigma, right, model.rate)
            exit_px, reason, exit_ts = model.quote(m).bid or 0.0, "tidsstop", ts
            break
        mins = minutes_to_close(ts + pd.Timedelta(minutes=barmin))
        fav, adv = (b["high"], b["low"]) if right == "C" else (b["low"], b["high"])
        m_adv = bs_price(adv, strike, mins, sigma, right, model.rate)
        m_fav = bs_price(fav, strike, mins, sigma, right, model.rate)
        # Konservativt: rammes begge i samme bar, antages stop loss først
        if m_adv <= sl_mid:
            exit_px, reason, exit_ts = model.quote(sl_mid).bid or 0.0, "stop loss", ts
            break
        if m_fav >= tp_mid:
            exit_px, reason, exit_ts = model.quote(tp_mid).bid or 0.0, "take profit", ts
            break
    if exit_px is None:
        last = day_bars.iloc[-1]
        exit_px = model.quote(bs_price(last["close"], strike, 1, sigma, right, model.rate)).bid or 0.0
        reason, exit_ts = "dagens slutning", day_bars.index[-1]

    fee = cfg.fee_per_contract
    pl = (exit_px - entry) * 100 - 2 * fee
    return Trade(str(d), right, ts0.strftime("%H:%M"), round(spot, 2), strike, round(vix, 2), entry,
                 exit_px, reason, exit_ts.strftime("%H:%M"), round(pl, 2), (exit_px - entry) / entry), "handel"


# ---------------------------------------------------------------------------
# Statistik og Monte Carlo
def longest_losing_streak(pls) -> int:
    best = cur = 0
    for p in pls:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def replay_all_in(trades: list[Trade], start: float, target: float, fee: float):
    cash = start
    path = []
    for tr in trades:
        n = position_size(cash, tr.entry, fee)
        if n == 0:
            path.append((tr.day, cash, 0, "for dyr"))
            continue
        cash += n * ((tr.exit - tr.entry) * 100 - 2 * fee)
        path.append((tr.day, round(cash, 2), n, tr.reason))
        if cash >= target:
            break
    return cash, path


def monte_carlo(trades: list[Trade], start: float, target: float, fee: float, runs: int = 10_000,
                max_steps: int = 2_000, seed: int = 1):
    rng = np.random.default_rng(seed)
    entries = np.array([t.entry for t in trades])
    exits = np.array([t.exit for t in trades])
    cost = entries * 100 + fee
    min_cost = cost.min()
    res = {"target": 0, "ruin": 0, "undecided": 0, "strict_ruin": 0, "strict_target": 0}
    steps_to_target = []
    for _ in range(runs):
        # Model A: dage med en for dyr kontrakt springes over (som botten gør).
        #          Ruin = saldoen er under prisen på den billigste kontrakt i data.
        cash = start
        outcome = "undecided"
        for step in range(max_steps):
            i = rng.integers(len(trades))
            n = math.floor(cash / cost[i] + 1e-9)
            if n > 0:
                cash += n * ((exits[i] - entries[i]) * 100 - 2 * fee)
            if cash >= target:
                outcome = "target"
                steps_to_target.append(step + 1)
                break
            if cash < min_cost:
                outcome = "ruin"
                break
        res[outcome] += 1
        # Model B (streng): ruin så snart dagens kontrakt ikke kan betales.
        cash = start
        for step in range(max_steps):
            i = rng.integers(len(trades))
            n = math.floor(cash / cost[i] + 1e-9)
            if n == 0:
                res["strict_ruin"] += 1
                break
            cash += n * ((exits[i] - entries[i]) * 100 - 2 * fee)
            if cash >= target:
                res["strict_target"] += 1
                break
    res["median_trades_to_target"] = float(np.median(steps_to_target)) if steps_to_target else None
    res["min_contract_cost"] = float(min_cost)
    res["runs"] = runs
    return res


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["yahoo", "ib", "csv"], default="yahoo")
    ap.add_argument("--days", type=int, default=500, help="antal dage ved --source ib")
    ap.add_argument("--index-csv")
    ap.add_argument("--vix-csv")
    ap.add_argument("--fixed-vix", type=float, help="brug fast volatilitet (VIX-niveau) i stedet for data")
    ap.add_argument("--spread-pct", type=float, default=0.06, help="modelleret spread som andel af mid")
    ap.add_argument("--vol-mult", type=float, default=1.0, help="skalering af VIX (0DTE-IV afviger fra VIX)")
    ap.add_argument("--runs", type=int, default=10_000)
    ap.add_argument("--start", type=float, default=150.0)
    ap.add_argument("--report", default="backtest_report.md")
    args = ap.parse_args(argv)

    cfg = Config()
    model = Model(spread_pct=args.spread_pct, vol_mult=args.vol_mult)
    if args.source == "yahoo":
        idx, vix_data, note = load_yahoo(DATA_DIR)
    elif args.source == "ib":
        idx, vix_data, note = load_ib(args.days)
    else:
        if not args.index_csv:
            ap.error("--index-csv kræves ved --source csv")
        idx, vix_data, note = load_csv(args.index_csv, args.vix_csv)

    idx = idx.between_time("09:30", "15:59")
    trades: list[Trade] = []
    skipped: dict[str, int] = {}
    days = sorted(set(idx.index.date))
    for d in days:
        if is_half_day(d):
            skipped["halv handelsdag"] = skipped.get("halv handelsdag", 0) + 1
            continue
        db = idx[idx.index.date == d]
        tr, why = simulate_day(db, vix_data, cfg, model, args.fixed_vix)
        if tr:
            trades.append(tr)
        else:
            key = why.split(":")[0]
            skipped[key] = skipped.get(key, 0) + 1

    fee = cfg.fee_per_contract
    lines = []
    w = lines.append
    w("# Backtest: XSP 0DTE opening range breakout\n")
    w(f"Kørt {datetime.now(ET):%Y-%m-%d %H:%M} ET.\n")
    w("> **VIGTIGT: Optionspriserne er ESTIMATER.** Der findes ingen gratis historiske 0DTE-optionspriser,")
    w("> og IB leverer ikke historik for udløbne optioner. Priserne er beregnet med Black-Scholes")
    w(f"> med VIX × {model.vol_mult:g} som volatilitet, tid til udløb i handelsminutter, og en modelleret")
    w(f"> bid/ask-spread på max(${model.min_spread:.2f}, {model.spread_pct:.0%} af mid). Køb antages fyldt på ask,")
    w("> salg på bid ved tærsklen. Virkelige 0DTE-priser (skew, IV-crush, gamma, spreads ved åbning)")
    w("> kan afvige betydeligt.\n")
    w(f"**Data:** {note}\n")
    w(f"Handelsdage i data: {len(days)}. Sprunget over: "
      + (", ".join(f"{k}: {v}" for k, v in skipped.items()) or "ingen") + "\n")
    if len(days) < 120:
        w(f"> ⚠️ **Lille stikprøve ({len(days)} dage).** Resultaterne er statistisk usikre. Kør med")
        w("> `--source ib --days 500` mod IB Gateway for et langt bedre grundlag.\n")

    if not trades:
        w("Ingen handler i perioden.")
        Path(args.report).write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return 0

    pls = [t.pl_1 for t in trades]
    wins = [t for t in trades if t.pl_1 > 0]
    losses = [t for t in trades if t.pl_1 <= 0]
    w("## Resultat pr. kontrakt (1 kontrakt pr. handel)\n")
    w("| Nøgletal | Værdi |\n|---|---|")
    w(f"| Antal handler | {len(trades)} |")
    w(f"| Vinderprocent | {len(wins) / len(trades):.1%} |")
    w(f"| Gns. gevinst | ${np.mean([t.pl_1 for t in wins]) if wins else 0:.2f} "
      f"({np.mean([t.ret for t in wins]) if wins else 0:+.0%}) |")
    w(f"| Gns. tab | ${np.mean([t.pl_1 for t in losses]) if losses else 0:.2f} "
      f"({np.mean([t.ret for t in losses]) if losses else 0:+.0%}) |")
    w(f"| Samlet P/L (1 kontrakt) | ${sum(pls):.2f} |")
    w(f"| Forventet værdi pr. handel | ${np.mean(pls):.2f} |")
    w(f"| Længste tabsstime | {longest_losing_streak(pls)} handler i træk |")
    reasons = pd.Series([t.reason for t in trades]).value_counts()
    w(f"| Exit-årsager | {', '.join(f'{k}: {v}' for k, v in reasons.items())} |")
    costs = np.array([t.entry * 100 + fee for t in trades])
    afford = (costs <= args.start).mean()
    w(f"| Gns. pris for én kontrakt | ${costs.mean():.2f} (min ${costs.min():.2f}, max ${costs.max():.2f}) |")
    w(f"| Andel handler råd til med ${args.start:.0f} | {afford:.0%} |\n")

    final, path = replay_all_in(trades, args.start, cfg.target_usd, fee)
    w(f"## Alt-ind, kronologisk genafspilning fra ${args.start:.0f}\n")
    w(f"Slutsaldo: **${final:.2f}** efter {len(path)} signaldage.\n")
    w("| Dato | Kontrakter | Saldo efter | Note |\n|---|---|---|---|")
    for d, cash, n, note in path:
        w(f"| {d} | {n} | ${cash:.2f} | {note} |")
    w("")

    mc = monte_carlo(trades, args.start, cfg.target_usd, fee, runs=args.runs)
    r = mc["runs"]
    w(f"## Monte Carlo, alt-ind ({r:,} kørsler, trækning med tilbagelægning af de {len(trades)} handelsudfald)\n")
    w("**Model A (som botten opfører sig):** dage hvor kontrakten er for dyr springes over.")
    w(f"Ruin = saldo under prisen på den billigste kontrakt i data (${mc['min_contract_cost']:.2f}).\n")
    w(f"- Når ${cfg.target_usd:.0f} før ruin: **{mc['target'] / r:.1%}**")
    w(f"- Ruin først: **{mc['ruin'] / r:.1%}**")
    w(f"- Uafgjort efter 2.000 handler: {mc['undecided'] / r:.1%}")
    if mc["median_trades_to_target"]:
        w(f"- Median antal signaldage til målet (når det nås): {mc['median_trades_to_target']:.0f}")
    w("\n**Model B (streng):** ruin så snart dagens kontrakt koster mere end saldoen.\n")
    w(f"- Når målet: **{mc['strict_target'] / r:.1%}**, ruin: **{mc['strict_ruin'] / r:.1%}**\n")

    w("## Alle handler\n")
    w("| Dato | Type | Signal | XSP | Strike | VIX | Entry (ask) | Exit (bid) | Årsag | Exit kl. | P/L 1 kt. |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for t in trades:
        w(f"| {t.day} | {'CALL' if t.right == 'C' else 'PUT'} | {t.signal_time} | {t.spot:.2f} | {t.strike:g} | "
          f"{t.vix:.1f} | {t.entry:.2f} | {t.exit:.2f} | {t.reason} | {t.exit_time} | {t.pl_1:+.2f} |")

    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    pd.DataFrame([asdict(t) for t in trades]).to_csv(Path(args.report).with_suffix(".csv"), index=False)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

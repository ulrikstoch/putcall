"""Daglig rebalancering mod IBKR (papir eller live).

Køres efter åbning (fx 09:45 ET). Vægte beregnes på afsluttede dage til og med
i går, præcis som i backtesten (signal ved lukning t -> handel dag t+1).
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import gates
from . import strategies as S
from .approvals import Approvals
from .data import completed_sessions_only, load_prices
from .engine import Costs, target_weights
from .registry import Candidate, Registry, candidate_hash

log = logging.getLogger("tradelab.executor")


class Refused(Exception):
    """Sikkerhedstjek fejlede - der handles ikke."""


@dataclass
class Order:
    symbol: str
    delta: float  # + køb, - salg (antal aktier)
    ref_price: float


def plan_orders(target_w: dict, positions: dict, prices: dict, alloc: float, band: float,
                fractional: bool, min_value: float = 1.0) -> list[Order]:
    total = alloc
    orders = []
    syms = sorted(set(target_w) | {s for s, q in positions.items() if q})
    for s in syms:
        px = prices.get(s)
        if not px:
            continue
        cur = positions.get(s, 0.0)
        cw = cur * px / total if total > 0 else 0.0
        tw = float(target_w.get(s, 0.0))
        change = abs(tw - cw) > band or (tw == 0 and cur > 0) or (tw > 0 and cur == 0)
        if not change:
            continue
        tgt = tw * total / px
        tgt = round(tgt, 4) if fractional else math.floor(tgt)
        delta = tgt - cur
        if tw == 0:
            delta = -cur
        if abs(delta) * px < min_value:
            continue
        orders.append(Order(s, delta, px))
    return sorted(orders, key=lambda o: o.delta)  # salg først


def select_candidate(mode: str, cfg, reg: Registry, approvals: Approvals) -> Candidate:
    if mode == "live":
        a = approvals.active
        if not a:
            raise Refused("Ingen aktiv godkendelse - kør 'python -m tradelab approve <id>'")
        c = reg.get(a["id"])
        cur = candidate_hash(S.get(c.strategy), c.params)
        if cur != a["hash"] or c.hash != a["hash"]:
            raise Refused(f"Koden/parametrene for {c.id} er ændret siden godkendelsen (hash {cur} != {a['hash']})")
        if c.status != "ready":
            raise Refused(f"{c.id} har status {c.status}, ikke 'ready'")
        return c
    if cfg.lab_paper_candidate:
        return reg.get(cfg.lab_paper_candidate)
    c = None
    pool = [x for x in reg.candidates if x.status in ("ready", "incubating")]
    if pool:
        c = max(pool, key=lambda x: (x.status == "ready", x.research.get("dsr", 0)))
    if c is None:
        raise Refused("Ingen inkuberende kandidater at papirhandle")
    return c


def check_mode(mode: str, cfg, accounts: list[str]) -> None:
    if mode == "live":
        if not cfg.is_live_port:
            raise Refused("Live-tilstand kræver IB_PORT=4001")
        if any(a.startswith("D") for a in accounts):
            raise Refused(f"Live-tilstand, men forbundet til papirkonto {accounts}")
    else:
        if cfg.is_live_port:
            raise Refused("Papir-tilstand må ikke bruge live-porten 4001")
        if accounts and not all(a.startswith("D") for a in accounts):
            raise Refused(f"Papir-tilstand, men kontoen {accounts} ligner IKKE en papirkonto (DU...)")


class LiveState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def update_peak(self, cid: str, equity: float) -> float:
        if self.data.get("candidate") != cid:
            self.data = {"candidate": cid, "peak": equity}
        self.data["peak"] = max(self.data.get("peak", equity), equity)
        self.data["last"] = equity
        self.path.write_text(json.dumps(self.data, indent=2))
        return self.data["peak"]


def rebalance(mode: str, cfg, broker, reg: Registry, approvals: Approvals, notifier, today: date,
              prices=None) -> str:
    tag = f"[{'LIVE' if mode == 'live' else 'PAPIR'}{' DRY' if cfg.dry_run else ''}] "
    broker.connect()
    check_mode(mode, cfg, broker.accounts())
    c = select_candidate(mode, cfg, reg, approvals)
    st = S.get(c.strategy)
    syms = st.universe(c.params)
    prices = prices or load_prices(syms, max_age_hours=1)
    prices = completed_sessions_only(prices, today)
    w = target_weights(st, c.params, prices.close[syms]).iloc[-1]
    signal_date = prices.close.index[-1].date()

    # Alle symboler labbet kan have positioner i (også fra tidligere kandidater)
    managed = set(syms)
    for x in reg.candidates:
        try:
            managed |= set(S.get(x.strategy).universe(x.params))
        except KeyError:
            pass
    positions = broker.positions(sorted(managed))
    active = [s for s in sorted(managed) if s in syms or positions.get(s)]
    quotes = {s: broker.quote(s) for s in active}
    ref = {}
    for s, (bid, ask, last) in quotes.items():
        fallback = float(prices.close[s].iloc[-1]) if s in prices.close else None
        px = (bid + ask) / 2 if bid and ask else (last or fallback)
        if px:
            ref[s] = px
    missing = [s for s in active if s not in ref]
    if missing:
        raise Refused(f"Ingen kurs for {missing} - handler ikke")
    cash = broker.cash_usd()
    equity = cash + sum(positions[s] * ref[s] for s in active)

    if mode == "live":
        cap = approvals.active["capital_cap"]
        peak = LiveState(cfg.path(cfg.lab_live_state_file)).update_peak(c.id, equity)
        dd = equity / peak - 1 if peak > 0 else 0.0
        if dd <= gates.LIVE["kill_dd"]:
            notifier.error(f"{tag}NØDSTOP: {c.id} er faldet {dd:.0%} fra toppen (${peak:.2f} -> ${equity:.2f}). "
                           "Sælger alt og tilbagekalder godkendelsen.")
            w = w * 0.0
            approvals.revoke(f"kill-switch drawdown {dd:.0%}")
            alloc = equity
        else:
            alloc = min(equity, cap)
    else:
        alloc = min(equity, cfg.lab_capital)

    orders = plan_orders(w.to_dict(), {s: positions[s] for s in active}, ref, alloc, Costs().band, cfg.lab_fractional)
    if not orders:
        msg = f"{tag}{c.id}: ingen ændringer (signal {signal_date}, værdi ${equity:.2f})"
        notifier.send(msg)
        return "no_change"

    lines = []
    for o in orders:
        bid, ask, last = quotes[o.symbol]
        if o.delta > 0:
            price = round((ask or ref[o.symbol]) * 1.002, 2)
            qty = o.delta
            avail = broker.cash_usd() if not cfg.dry_run else cash
            if qty * price > avail:
                qty = round(max(0.0, (avail - 1.0) / price), 4) if cfg.lab_fractional else math.floor((avail - 1.0) / price)
            if qty * price < 1.0:
                continue
            action = "BUY"
        else:
            price = round((bid or ref[o.symbol]) * 0.998, 2)
            qty = -o.delta
            action = "SELL"
        if cfg.dry_run:
            filled, avg = qty, price
            cash += -qty * price if action == "BUY" else qty * price
        else:
            t = broker.place_limit(o.symbol, action, qty, price)
            waited = 0.0
            f = broker.status(t)
            while not f.done and waited < cfg.lab_order_timeout_sec:
                broker.sleep(1.0)
                waited += 1.0
                f = broker.status(t)
            if not f.done:
                broker.cancel(t)
                f = broker.status(t)
            filled, avg = f.filled, f.avg_price or price
        model_px = float(prices.close[o.symbol].iloc[-1])
        _log_fill(cfg.path(cfg.lab_fills_file), today, mode, c.id, o.symbol, action, qty, filled, avg, model_px,
                  cfg.dry_run)
        lines.append(f"{action} {filled:g}/{qty:g} {o.symbol} @ {avg:.2f}")
    notifier.send(f"{tag}{c.id} rebalancering (signal {signal_date}): " + "; ".join(lines)
                  + f". Kontoværdi ca. ${equity:.2f}")
    return "traded"


def _log_fill(path: Path, today, mode, cid, sym, action, qty, filled, avg, model_px, dry):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["dato", "mode", "kandidat", "symbol", "handling", "bestilt", "fyldt", "pris",
                        "model_pris_i_gaar", "dry_run"])
        w.writerow([today, mode, cid, sym, action, f"{qty:.4f}", f"{filled:.4f}", f"{avg:.4f}",
                    f"{model_px:.4f}", int(dry)])

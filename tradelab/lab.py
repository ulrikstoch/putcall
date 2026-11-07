"""Research, registrering og fremadrettet evaluering af kandidater."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import gates, metrics
from . import strategies as S
from .data import Prices, load_prices
from .engine import Costs, lookahead_check, run
from .registry import Candidate, Registry, candidate_hash

WARMUP_DAYS = 260


def research_start(close: pd.DataFrame) -> pd.Timestamp:
    """Første dag hvor alle symboler har data + opvarmning til indikatorer."""
    full = close.dropna(how="any")
    if full.empty:
        raise ValueError("Ingen periode hvor alle symboler har data")
    i = close.index.get_loc(full.index[0]) + WARMUP_DAYS
    return close.index[min(i, len(close.index) - 1)]


def evaluate_research(strategy_key: str, overrides: dict | None, reg: Registry, prices: Prices | None = None,
                      capital: float = 150.0, costs: Costs | None = None, log_trial: bool = True,
                      end=None) -> dict:
    st = S.get(strategy_key)
    params = st.params(overrides)
    prices = prices or load_prices(st.universe(params))
    if end is not None:
        prices = prices.upto(end)
    close = prices.close[st.universe(params)]
    start = research_start(close)
    res = run(st, params, prices, start=start, capital=capital, costs=costs)
    summ = metrics.summary(res.equity, len(res.trades))
    h = candidate_hash(st, params)
    if log_trial:
        reg.log_trial(strategy_key, params, h, summ["sharpe"], summ["days"], "research")
    n, srs = reg.trial_stats()
    dsr = metrics.deflated_sharpe(res.returns, max(n, 1), srs)
    sub = metrics.subperiod_returns(res.equity, 4)
    la = lookahead_check(st, params, prices)
    bench = run(S.get("BuyHold_v1"), {"symbol": "SPY"},
                prices if "SPY" in prices.close else load_prices(["SPY"]),
                start=start, capital=capital, costs=costs)
    bench_s = metrics.summary(bench.equity, len(bench.trades))
    ok, fails = gates.research_gate(summ, dsr, sub, bench_s)
    if la:
        ok = False
        fails = fails + ["LOOK-AHEAD: " + "; ".join(la)]
    return {
        "strategy": strategy_key, "params": params, "hash": h, "summary": summ, "dsr": dsr,
        "subperiods": sub, "n_trials": n, "passed": ok, "fails": fails,
        "benchmark_spy": bench_s,
        "costs_paid": res.costs_paid, "result": res,
    }


def register(strategy_key: str, overrides: dict | None, reg: Registry, note: str, today: date,
             prices: Prices | None = None, capital: float = 150.0, force: bool = False) -> tuple[Candidate | None, dict]:
    rep = evaluate_research(strategy_key, overrides, reg, prices, capital)
    if reg.by_hash(rep["hash"]):
        raise ValueError(f"Findes allerede som {reg.by_hash(rep['hash']).id}")
    if not rep["passed"] and not force:
        return None, rep
    c = Candidate(
        id=reg.next_id(strategy_key), strategy=strategy_key, params=rep["params"], hash=rep["hash"],
        status="incubating", registered=today.isoformat(), note=note,
        research={k: rep[k] for k in ("summary", "dsr", "subperiods", "n_trials", "benchmark_spy")},
    )
    c.log(f"registreret ({'research-gate bestået' if rep['passed'] else 'TVUNGET uden gate'})")
    reg.add(c)
    reg.save()
    return c, rep


def forward_result(c: Candidate, prices: Prices, capital: float, costs: Costs | None = None):
    st = S.get(c.strategy)
    start = pd.Timestamp(c.registered) + timedelta(days=1)
    if prices.close.index[-1] < start:
        return None
    return run(st, c.params, prices, start=start, capital=capital, costs=costs)


def evaluate_all(reg: Registry, capital: float = 150.0, prices: Prices | None = None, save: bool = True) -> list[str]:
    """Opdatér forward-tal og status for alle kandidater. Returnerer ændringer."""
    changes = []
    live = [c for c in reg.candidates if c.status in ("incubating", "ready")]
    if not live:
        return changes
    syms = sorted({s for c in live for s in S.get(c.strategy).universe(c.params)} | {"SPY"})
    prices = prices or load_prices(syms)
    for c in live:
        try:
            st = S.get(c.strategy)
        except KeyError:
            c.status = "retired"
            c.log("pensioneret: strategiklassen findes ikke længere")
            changes.append(f"{c.id}: pensioneret (klasse slettet)")
            continue
        if candidate_hash(st, c.params) != c.hash:
            c.status = "retired"
            c.log("pensioneret: koden er ændret efter registrering")
            changes.append(f"{c.id}: pensioneret (kode ændret efter registrering)")
            continue
        res = forward_result(c, prices, capital)
        if res is None:
            continue
        summ = metrics.summary(res.equity, len(res.trades))
        c.forward = summ
        ok, fails = gates.forward_gate(summ)
        c.forward["fails"] = fails
        if summ["max_dd"] < gates.RETIRE_DD:
            c.status = "retired"
            c.log(f"pensioneret: forward drawdown {summ['max_dd']:.0%}")
            changes.append(f"{c.id}: pensioneret (drawdown {summ['max_dd']:.0%})")
        elif ok and c.status == "incubating":
            c.status = "ready"
            c.log("forward-gate bestået - klar til menneskelig godkendelse")
            changes.append(f"{c.id}: KLAR til godkendelse")
        elif not ok and c.status == "ready":
            c.status = "incubating"
            c.log("forward-gate ikke længere opfyldt: " + "; ".join(fails))
            changes.append(f"{c.id}: tilbage til inkubation")
    if save:
        reg.save()
    return changes


def status_markdown(reg: Registry) -> str:
    n, _ = reg.trial_stats()
    lines = ["# Lab-status", "", f"Unikke forsøg i alt: **{n}** (bruges i Deflated Sharpe).", ""]
    lines += ["| Kandidat | Status | Registreret | Research Sharpe / DSR | Forward dage | Forward afkast | Forward DD | Mangler |",
              "|---|---|---|---|---|---|---|---|"]
    for c in reg.candidates:
        r = c.research.get("summary", {})
        f = c.forward or {}
        lines.append(
            f"| {c.id} | {c.status} | {c.registered} | {r.get('sharpe', 0):.2f} / {c.research.get('dsr', 0):.2f} | "
            f"{f.get('days', 0)} | {f.get('total_return', 0):+.1%} | {f.get('max_dd', 0):.0%} | "
            f"{'; '.join(f.get('fails', [])) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def best_incubating(reg: Registry) -> Candidate | None:
    pool = [c for c in reg.candidates if c.status in ("ready", "incubating")]
    if not pool:
        return None
    return max(pool, key=lambda c: (c.status == "ready", c.research.get("dsr", 0)))

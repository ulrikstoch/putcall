"""tradelab CLI.

  python -m tradelab list                         # strategier og parametre
  python -m tradelab backtest KEY [--params JSON] # backtest (logges som forsøg)
  python -m tradelab register KEY --params JSON --note "hypotese"
  python -m tradelab evaluate [--notify]          # forward-evaluering, opdaterer status
  python -m tradelab status
  python -m tradelab approve ID --capital 150     # KUN menneske, på VPS'en
  python -m tradelab revoke
  python -m tradelab trade --mode paper|live      # daglig rebalancering mod IBKR
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from xspbot.calendar import now_et
from xspbot.config import ConfigError
from xspbot.notify import Notifier

from . import lab
from . import strategies as S
from .approvals import Approvals
from .config import LabConfig
from .registry import LAB_DIR, Registry, candidate_hash


def _fmt(rep: dict) -> str:
    s, b = rep["summary"], rep["benchmark_spy"]
    out = [
        f"{rep['strategy']} {json.dumps(rep['params'])}",
        f"  periode {s['start']}..{s['end']} ({s['days'] / 252:.1f} år), handler {s['trades']}, "
        f"omkostninger ${rep['costs_paid']:.2f}",
        f"  CAGR {s['cagr']:+.1%}  Sharpe {s['sharpe']:.2f}  maxDD {s['max_dd']:.0%}  "
        f"slut ${s['final']:.2f}",
        f"  SPY køb-og-hold: CAGR {b['cagr']:+.1%}  Sharpe {b['sharpe']:.2f}  maxDD {b['max_dd']:.0%}",
        f"  Deflated Sharpe {rep['dsr']:.2f} (unikke forsøg: {rep['n_trials']}), "
        f"delperioder {', '.join(f'{x:+.0%}' for x in rep['subperiods'])}",
        "  RESEARCH-GATE: " + ("BESTÅET" if rep["passed"] else "IKKE bestået: " + "; ".join(rep["fails"])),
    ]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tradelab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("backtest", "register"):
        p = sub.add_parser(name)
        p.add_argument("strategy")
        p.add_argument("--params", default="{}")
        p.add_argument("--note", default="")
        p.add_argument("--end", help="kun data til og med denne dato")
    e = sub.add_parser("evaluate")
    e.add_argument("--notify", action="store_true")
    e.add_argument("--no-save", action="store_true", help="skriv ikke registry/STATUS (bruges på VPS'en)")
    sub.add_parser("status")
    a = sub.add_parser("approve")
    a.add_argument("id")
    a.add_argument("--capital", type=float, required=True, help="maks USD botten må styre live")
    sub.add_parser("revoke")
    t = sub.add_parser("trade")
    t.add_argument("--mode", choices=["paper", "live"], required=True)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("ib_async").setLevel(logging.WARNING)
    try:
        cfg = LabConfig.from_env()
    except ConfigError as exc:
        print("Konfigurationsfejl:", exc)
        return 2
    reg = Registry()
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id, prefix="Tradelab ", background=False)

    if args.cmd == "list":
        for k, cls in sorted(S.STRATEGIES.items()):
            print(f"{k}: {cls.description}\n    defaults: {json.dumps(cls.defaults)}")
        return 0

    if args.cmd == "backtest":
        rep = lab.evaluate_research(args.strategy, json.loads(args.params), reg, capital=cfg.lab_capital,
                                    end=args.end)
        print(_fmt(rep))
        return 0

    if args.cmd == "register":
        if not args.note:
            print("Angiv --note med hypotesen bag kandidaten")
            return 2
        c, rep = lab.register(args.strategy, json.loads(args.params), reg, args.note, now_et().date(),
                              capital=cfg.lab_capital)
        print(_fmt(rep))
        print(f"\nRegistreret som {c.id} - inkubation starter i morgen." if c else "\nIKKE registreret.")
        return 0 if c else 1

    if args.cmd == "evaluate":
        changes = lab.evaluate_all(reg, capital=cfg.lab_capital, save=not args.no_save)
        md = lab.status_markdown(reg)
        if not args.no_save:
            (LAB_DIR / "STATUS.md").write_text(md, encoding="utf-8")
        print(md)
        if changes:
            print("Ændringer:\n  " + "\n  ".join(changes))
            if args.notify:
                notifier.send("Lab-ændringer: " + "; ".join(changes))
        return 0

    if args.cmd == "status":
        print(lab.status_markdown(reg))
        ap_ = Approvals(cfg.path(cfg.lab_approvals_file))
        print("Aktiv live-godkendelse:", ap_.active or "ingen")
        return 0

    if args.cmd == "approve":
        c = reg.get(args.id)
        cur = candidate_hash(S.get(c.strategy), c.params)
        print(f"Kandidat {c.id}: {c.strategy} {json.dumps(c.params)}")
        print(f"  Hypotese: {c.note}")
        print(f"  Research: {json.dumps(c.research.get('summary', {}))}  DSR {c.research.get('dsr', 0):.2f}")
        print(f"  Forward:  {json.dumps({k: v for k, v in c.forward.items() if k != 'fails'})}")
        if c.status != "ready":
            print(f"\nAfvist: status er '{c.status}', ikke 'ready'. Mangler: {c.forward.get('fails')}")
            return 1
        if cur != c.hash:
            print("\nAfvist: koden er ændret efter registrering.")
            return 1
        if not sys.stdin.isatty():
            print("\nGodkendelse kræver en interaktiv terminal (et menneske).")
            return 1
        print(f"\nDu giver botten lov til at handle med op til ${args.capital:.2f} af RIGTIGE penge.")
        print("Strategien kan tabe penge. Den stoppes automatisk ved et fald på "
              f"{-__import__('tradelab.gates', fromlist=['LIVE']).LIVE['kill_dd']:.0%} fra toppen.")
        if input(f"Skriv kandidatens id ({c.id}) for at godkende: ").strip() != c.id:
            print("Ikke godkendt.")
            return 1
        rec = Approvals(cfg.path(cfg.lab_approvals_file)).approve(c.id, c.hash, args.capital, getpass.getuser())
        print("Godkendt:", rec)
        notifier.send(f"{c.id} GODKENDT til live med maks ${args.capital:.2f}")
        return 0

    if args.cmd == "revoke":
        Approvals(cfg.path(cfg.lab_approvals_file)).revoke("manuelt")
        print("Live-godkendelse tilbagekaldt.")
        notifier.send("Live-godkendelse tilbagekaldt manuelt")
        return 0

    if args.cmd == "trade":
        from .broker import StockBroker
        from .executor import Refused, rebalance

        broker = StockBroker(cfg)
        try:
            out = rebalance(args.mode, cfg, broker, reg, Approvals(cfg.path(cfg.lab_approvals_file)), notifier,
                            now_et().date())
            print(out)
            return 0
        except Refused as exc:
            notifier.error(f"Handel afvist ({args.mode}): {exc}")
            return 1
        except Exception as exc:
            logging.exception("Fejl under rebalancering")
            notifier.error(f"Fejl under rebalancering ({args.mode}): {type(exc).__name__}: {exc}")
            return 1
        finally:
            broker.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())

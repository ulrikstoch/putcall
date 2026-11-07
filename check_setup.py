#!/usr/bin/env python3
"""Tjek opsætningen FØR botten kører: forbindelse, saldo, XSP-kurs og dagens optionskæde.

Sender ingen ordrer.   Brug:  python check_setup.py [--delayed]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xspbot.calendar import is_half_day, now_et  # noqa: E402
from xspbot.config import Config, ConfigError  # noqa: E402
from xspbot.notify import Notifier  # noqa: E402
from xspbot.pricing import entry_filter, pick_strike, position_size, round_to_tick  # noqa: E402


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg):
    print(f"  [!!]   {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delayed", action="store_true", help="brug forsinkede data (hvis abonnement mangler)")
    ap.add_argument("--telegram", action="store_true", help="send en testbesked til Telegram")
    args = ap.parse_args()

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print("Konfigurationsfejl:", exc)
        return 2
    if args.delayed:
        cfg.market_data_type = 3

    print(f"\n== Konfiguration ==")
    print(f"  Port {cfg.ib_port} ({'LIVE' if cfg.is_live else 'PAPER'}), DRY_RUN={cfg.dry_run}, "
          f"gebyr/kontrakt ${cfg.fee_per_contract:.2f}, mål ${cfg.target_usd:.0f}")
    today = now_et().date()
    print(f"  Dato (ET): {today}  {'HALV HANDELSDAG - botten handler ikke' if is_half_day(today) else ''}")
    print(f"  STOP-fil: {'FINDES' if cfg.stop_path.exists() else 'findes ikke'}")

    if args.telegram:
        n = Notifier(cfg.telegram_token, cfg.telegram_chat_id, background=False)
        if n.enabled:
            n.send("XSP-bot: testbesked fra check_setup.py")
            ok("Telegram-testbesked sendt (tjek din telefon)")
        else:
            warn("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID er ikke sat")

    from xspbot.broker import IBBroker

    brk = IBBroker(cfg)
    print("\n== Forbindelse ==")
    try:
        brk.connect()
    except Exception as exc:
        warn(f"Kunne ikke forbinde til {cfg.ib_host}:{cfg.ib_port}: {exc}")
        print("     Kører IB Gateway? Er API slået til, og er porten rigtig?")
        return 1
    ib = brk.ib
    ok(f"Forbundet. Konti: {ib.managedAccounts()}  server-version {ib.client.serverVersion()}")

    print("\n== Konto ==")
    for v in ib.accountValues(cfg.ib_account):
        if v.tag in ("CashBalance", "TotalCashValue", "AvailableFunds", "NetLiquidation", "SettledCash") and \
                v.currency in ("USD", "DKK", "BASE"):
            print(f"  {v.tag:16s} {v.currency:5s} {v.value}")
        if v.tag == "AccountType":
            print(f"  Kontotype: {v.value}")
    try:
        cash = brk.cash_usd()
        ok(f"Disponibel USD til botten: ${cash:.2f}")
    except Exception as exc:
        cash = 0.0
        warn(f"Ingen USD-kontant fundet ({exc}). Har du vekslet DKK til USD?")
    pos = brk.option_positions()
    print(f"  Åbne XSP-optionspositioner: {len(pos)}")
    for p in pos:
        print(f"    {p.description} x{p.qty} avgCost {p.avg_cost:.2f} (entry {p.avg_cost / cfg.multiplier:.2f})")

    print("\n== Markedsdata XSP-indeks ==")
    price = None
    for _ in range(5):
        price = brk.index_price()
        if price:
            break
        brk.sleep(1)
    if price:
        ok(f"XSP = {price:.2f}")
    else:
        warn("Ingen XSP-kurs. Kræver Cboe-indeksdata (eller markedet er lukket). Prøv evt. --delayed")

    print("\n== Optionskæde ==")
    expirations, strikes = brk.chain(today)
    exp = today.strftime("%Y%m%d")
    upcoming = sorted(e for e in expirations if e >= exp)[:5]
    print(f"  Næste udløb: {', '.join(upcoming)}")
    if exp in expirations:
        ok(f"Udløb i dag ({exp}) findes. {len(strikes)} strikes: {strikes[0]:g} .. {strikes[-1]:g}")
    else:
        warn("Intet udløb i dag - botten handler ikke i dag (weekend/helligdag?)")

    ref = price
    if exp in expirations and strikes:
        ref = ref or strikes[len(strikes) // 2]
        print(f"\n  Kurser omkring {ref:.2f} (dagens udløb):")
        print(f"  {'kontrakt':28s} {'bid':>6s} {'ask':>6s} {'mid':>6s}  filter / antal ved ${cash:.0f}")
        for right in ("C", "P"):
            k = pick_strike(strikes, ref, right)
            if k is None:
                continue
            c = brk.option(exp, k, right)
            q = brk.quote(c)
            why = entry_filter(q, cfg.min_mid, cfg.max_spread_pct) or "OK"
            n = position_size(cash, round_to_tick(q.ask, "up"), cfg.fee_per_contract) if q.has_ask else 0
            fmt = lambda x: f"{x:6.2f}" if x is not None and not math.isnan(x) else "   n/a"
            print(f"  {brk.describe(c):28s} {fmt(q.bid)} {fmt(q.ask)} {fmt(q.mid)}  {why} / {n} stk.")
            if q.has_ask and n == 0:
                warn(f"For lidt kontant til én kontrakt (kræver ${q.ask * 100 + cfg.fee_per_contract:.2f})")

    brk.disconnect()
    print("\nFærdig. Ingen ordrer blev sendt.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

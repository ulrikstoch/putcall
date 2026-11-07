"""Kør botten:  python -m xspbot  [--reset-target] [--status]"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .calendar import at, now_et
from .config import Config, ConfigError
from .notify import Notifier
from .state import StateStore
from .tradelog import setup_logging

log = logging.getLogger("xspbot")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xspbot", description="XSP 0DTE opening range breakout bot")
    ap.add_argument("--reset-target", action="store_true", help="Nulstil 'mål nået'-flaget i state.json")
    ap.add_argument("--status", action="store_true", help="Vis state.json og afslut")
    args = ap.parse_args(argv)

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"Konfigurationsfejl: {exc}", file=sys.stderr)
        return 2

    store = StateStore(cfg.state_path)
    if args.status:
        print(store.load())
        return 0
    if args.reset_target:
        print("Nulstillet:", store.reset_done())
        return 0

    setup_logging(cfg.log_path)
    mode = "LIVE" if cfg.is_live else "PAPER"
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id, prefix=f"XSP-bot [{mode}] ")
    log.info("Konfiguration: %s", cfg.summary())

    from .broker import IBBroker
    from .engine import Bot

    broker = IBBroker(cfg)
    bot = Bot(cfg, broker, notifier, store)
    rc = 0
    for attempt in range(1, cfg.max_crash_retries + 1):
        try:
            outcome = bot.run()
            log.info("Dagens udfald: %s", outcome)
            break
        except KeyboardInterrupt:
            notifier.error("Botten blev stoppet manuelt (Ctrl-C). Tjek om der er åbne positioner!")
            rc = 130
            break
        except Exception as exc:
            log.exception("Uventet fejl (forsøg %d)", attempt)
            if attempt <= 3 or attempt % 20 == 0:  # undgå spam ved længere udfald
                notifier.error(
                    f"Uventet fejl (forsøg {attempt}): {type(exc).__name__}: {exc}. "
                    f"Prøver igen om {cfg.crash_retry_sec:.0f} sek."
                )
            broker.disconnect()
            now = now_et()
            if now >= at(now.date(), cfg.market_close):
                notifier.error("Markedet er lukket - giver op. Tjek kontoen for åbne positioner!")
                rc = 1
                break
            time.sleep(cfg.crash_retry_sec)
    else:
        notifier.error(
            f"Giver op efter {cfg.max_crash_retries} fejl. EN POSITION KAN VÆRE UOVERVÅGET - tjek kontoen nu!"
        )
        rc = 1
    broker.disconnect()
    notifier.flush()
    return rc


if __name__ == "__main__":
    sys.exit(main())

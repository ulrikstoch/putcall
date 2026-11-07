"""Logning til bot.log og trades.csv."""

from __future__ import annotations

import csv
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

TRADE_FIELDS = ["dato", "kontrakt", "antal", "entry", "exit", "aarsag", "pl_usd", "dry_run"]


def setup_logging(log_path: Path, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=10, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("ib_async").setLevel(logging.WARNING)


def append_trade(path: Path, row: dict) -> None:
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_FIELDS})

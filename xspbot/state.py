"""Persistent tilstand i state.json (atomisk skrivning)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BotState:
    last_trade_date: Optional[str] = None  # ISO-dato (ET)
    done: bool = False  # mål nået -> permanent stop
    done_reason: Optional[str] = None
    done_at: Optional[str] = None


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> BotState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BotState()
        known = {k: raw[k] for k in BotState.__dataclass_fields__ if k in raw}
        return BotState(**known)

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(state), fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # Hjælpere -----------------------------------------------------------
    def mark_trade_date(self, iso_date: str) -> BotState:
        s = self.load()
        s.last_trade_date = iso_date
        self.save(s)
        return s

    def clear_trade_date(self, iso_date: str) -> BotState:
        s = self.load()
        if s.last_trade_date == iso_date:
            s.last_trade_date = None
            self.save(s)
        return s

    def mark_done(self, reason: str, when: str) -> BotState:
        s = self.load()
        s.done = True
        s.done_reason = reason
        s.done_at = when
        self.save(s)
        return s

    def reset_done(self) -> BotState:
        s = self.load()
        s.done = False
        s.done_reason = None
        s.done_at = None
        self.save(s)
        return s

"""Menneskelig godkendelse til live-handel. Filen ligger KUN lokalt på VPS'en (gitignored).

En godkendelse gælder præcis én kandidat-hash. Ændres strategiens kode, indicators.py
eller parametrene, passer hashen ikke længere, og live-handel nægtes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Approvals:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"active": None, "history": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @property
    def active(self) -> Optional[dict]:
        return self.data.get("active")

    def approve(self, cid: str, h: str, capital_cap: float, who: str) -> dict:
        rec = {
            "id": cid, "hash": h, "capital_cap": float(capital_cap), "by": who,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if self.active:
            self.data["history"].append({**self.active, "ended": rec["at"], "reason": "erstattet"})
        self.data["active"] = rec
        self.save()
        return rec

    def revoke(self, reason: str) -> None:
        if self.active:
            self.data["history"].append(
                {**self.active, "ended": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason}
            )
        self.data["active"] = None
        self.save()

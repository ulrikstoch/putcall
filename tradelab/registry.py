"""Kandidat-register (lab/registry.json) og forsøgslog (lab/trials.jsonl).

Hver backtest logges som et forsøg. Antallet af forsøg bruges i Deflated Sharpe,
så jo mere der prøves, jo højere bliver kravet.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import indicators
from .strategies import Strategy

ROOT = Path(__file__).resolve().parent.parent
LAB_DIR = ROOT / "lab"
REGISTRY = LAB_DIR / "registry.json"
TRIALS = LAB_DIR / "trials.jsonl"

STATUSES = ("incubating", "ready", "retired")


def candidate_hash(strategy: type[Strategy], params: dict) -> str:
    h = hashlib.sha256()
    h.update(strategy.source().encode())
    h.update(Path(indicators.__file__).read_bytes())
    h.update(json.dumps(params, sort_keys=True).encode())
    return h.hexdigest()[:16]


@dataclass
class Candidate:
    id: str
    strategy: str
    params: dict
    hash: str
    status: str
    registered: str  # ISO-dato: inkubation starter dagen efter
    note: str = ""
    research: dict = field(default_factory=dict)
    forward: dict = field(default_factory=dict)
    history: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.history.append(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z {msg}")


class Registry:
    def __init__(self, path: Path = REGISTRY, trials_path: Path = TRIALS):
        self.path = Path(path)
        self.trials_path = Path(trials_path)
        self.candidates: list[Candidate] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.candidates = [Candidate(**c) for c in raw.get("candidates", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"candidates": [asdict(c) for c in self.candidates]}
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def get(self, cid: str) -> Candidate:
        for c in self.candidates:
            if c.id == cid:
                return c
        raise KeyError(f"Ukendt kandidat {cid!r}")

    def by_hash(self, h: str):
        return next((c for c in self.candidates if c.hash == h), None)

    def add(self, c: Candidate) -> None:
        if self.by_hash(c.hash):
            raise ValueError(f"Kandidat med samme kode og parametre findes allerede: {self.by_hash(c.hash).id}")
        if any(x.id == c.id for x in self.candidates):
            raise ValueError(f"id {c.id} findes allerede")
        self.candidates.append(c)

    def next_id(self, strategy_key: str) -> str:
        n = sum(1 for c in self.candidates if c.strategy == strategy_key) + 1
        return f"{strategy_key}-{n:03d}"

    # -- forsøg -------------------------------------------------------------
    def log_trial(self, strategy_key: str, params: dict, h: str, sharpe: float, days: int, kind: str) -> None:
        self.trials_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "strategy": strategy_key, "params": params, "hash": h,
            "sharpe": round(float(sharpe), 4), "days": days, "kind": kind,
        }
        with self.trials_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def trials(self) -> list[dict]:
        if not self.trials_path.exists():
            return []
        out = []
        for line in self.trials_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def trial_stats(self) -> tuple[int, list[float]]:
        """Antal UNIKKE forsøg (hash) og deres Sharpe-værdier."""
        best: dict[str, float] = {}
        for t in self.trials():
            best[t["hash"]] = t["sharpe"]
        return len(best), list(best.values())

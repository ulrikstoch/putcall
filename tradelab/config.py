"""Konfiguration for tradelab (miljøvariabler, evt. fra .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from xspbot.config import LIVE_PORT, PAPER_PORT, ConfigError, read_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class LabConfig:
    ib_host: str = "127.0.0.1"
    ib_port: int = PAPER_PORT
    lab_client_id: int = 27
    ib_account: str = ""
    market_data_type: int = 1
    dry_run: bool = False
    lab_capital: float = 150.0  # kapital der testes og handles med
    lab_fractional: bool = True
    lab_paper_candidate: str = ""  # tom = bedste inkuberende kandidat
    lab_approvals_file: str = "approvals.json"  # LOKAL fil, ikke i git
    lab_live_state_file: str = "lab_live_state.json"
    lab_fills_file: str = "lab_fills.csv"
    lab_order_timeout_sec: float = 60.0
    telegram_token: str = ""
    telegram_chat_id: str = ""
    project_dir: Path = ROOT

    def path(self, name: str) -> Path:
        p = Path(name)
        return p if p.is_absolute() else self.project_dir / p

    @property
    def is_live_port(self) -> bool:
        return self.ib_port == LIVE_PORT

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "LabConfig":
        env = dict(os.environ if environ is None else environ)
        if environ is None:
            for k, v in read_dotenv(ROOT / ".env").items():
                env.setdefault(k, v)
        cfg = cls()
        for f in fields(cls):
            key = f.name.upper()
            if key not in env or env[key] == "":
                continue
            cur, raw = getattr(cfg, f.name), env[key]
            try:
                if isinstance(cur, bool):
                    val = raw.strip().lower() in {"1", "true", "yes", "ja", "on"}
                elif isinstance(cur, int):
                    val = int(raw)
                elif isinstance(cur, float):
                    val = float(raw)
                elif isinstance(cur, Path):
                    val = Path(raw)
                else:
                    val = raw
            except ValueError as exc:
                raise ConfigError(f"Ugyldig værdi for {key}: {raw!r}") from exc
            setattr(cfg, f.name, val)
        if cfg.ib_port == LIVE_PORT and env.get("IB_PORT", "").strip() != str(LIVE_PORT):
            raise ConfigError("Port 4001 (LIVE) kræver eksplicit IB_PORT=4001")
        return cfg

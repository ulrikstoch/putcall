"""Al konfiguration samlet i én dataclass.

Hvert felt kan overskrives med en miljøvariabel med samme navn i
STORE BOGSTAVER (fx ``IB_PORT=4001``, ``DRY_RUN=1``). En ``.env``-fil i
projektmappen indlæses også, men overskriver aldrig variabler, der
allerede er sat i miljøet.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

PAPER_PORT = 4002
LIVE_PORT = 4001


class ConfigError(Exception):
    pass


def _parse_time(value: str) -> time:
    hh, mm = value.strip().split(":")[:2]
    return time(int(hh), int(mm))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass
class Config:
    # --- Forbindelse -----------------------------------------------------
    ib_host: str = "127.0.0.1"
    ib_port: int = PAPER_PORT  # 4002 = paper (standard), 4001 = live
    ib_client_id: int = 17
    ib_account: str = ""  # tom = første/eneste konto
    market_data_type: int = 1  # 1=live, 3=delayed (kun til test)

    # --- Drift -----------------------------------------------------------
    dry_run: bool = False
    project_dir: Path = PROJECT_DIR
    state_file: str = "state.json"
    log_file: str = "bot.log"
    trades_file: str = "trades.csv"
    stop_file: str = "STOP"

    # --- Penge -----------------------------------------------------------
    target_usd: float = 1450.0
    fee_per_contract: float = 1.10

    # --- Instrument ------------------------------------------------------
    symbol: str = "XSP"
    index_exchange: str = "CBOE"
    trading_class: str = "XSP"
    option_exchange: str = "SMART"
    currency: str = "USD"
    multiplier: int = 100

    # --- Strategi (alle tider America/New_York) -------------------------
    timezone: str = "America/New_York"
    range_start: time = time(9, 30)
    range_end: time = time(10, 0)
    entry_deadline: time = time(13, 30)
    time_stop: time = time(15, 0)
    market_close: time = time(16, 0)
    breakout_buffer: float = 0.25
    min_mid: float = 0.15
    max_spread_pct: float = 0.20
    take_profit_pct: float = 1.00  # +100 %
    stop_loss_pct: float = 0.50  # -50 %
    max_trades_per_day: int = 1

    # --- Ordrehåndtering -------------------------------------------------
    entry_timeout_sec: float = 20.0
    exit_timeout_sec: float = 8.0
    exit_max_attempts: int = 8
    poll_interval_sec: float = 1.0
    data_stale_alert_sec: float = 60.0
    crash_retry_sec: float = 15.0
    max_crash_retries: int = 2000  # stopper alligevel ved market_close

    # --- Notifikationer --------------------------------------------------
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Hvilke felter der eksplicit kom fra miljøet (bruges til live-tjek)
    _from_env: set = field(default_factory=set, repr=False)

    # ------------------------------------------------------------------
    def path(self, name: str) -> Path:
        p = Path(name)
        return p if p.is_absolute() else self.project_dir / p

    @property
    def state_path(self) -> Path:
        return self.path(self.state_file)

    @property
    def log_path(self) -> Path:
        return self.path(self.log_file)

    @property
    def trades_path(self) -> Path:
        return self.path(self.trades_file)

    @property
    def stop_path(self) -> Path:
        return self.path(self.stop_file)

    @property
    def is_live(self) -> bool:
        return self.ib_port == LIVE_PORT

    def validate(self, environ: dict | None = None) -> None:
        environ = os.environ if environ is None else environ
        if self.ib_port == LIVE_PORT and environ.get("IB_PORT", "").strip() != str(LIVE_PORT):
            raise ConfigError(
                "Port 4001 (LIVE) må kun bruges, når miljøvariablen IB_PORT=4001 er sat eksplicit."
            )
        if self.fee_per_contract < 0:
            raise ConfigError("fee_per_contract skal være >= 0")
        if not (0 < self.stop_loss_pct < 1):
            raise ConfigError("stop_loss_pct skal ligge mellem 0 og 1")
        if self.max_trades_per_day != 1:
            raise ConfigError("Botten understøtter kun én handel om dagen")

    @classmethod
    def from_env(cls, environ: dict | None = None, load_dotenv: bool = True) -> "Config":
        env = dict(os.environ if environ is None else environ)
        if load_dotenv and environ is None:
            for k, v in read_dotenv(PROJECT_DIR / ".env").items():
                env.setdefault(k, v)
                os.environ.setdefault(k, v)
        cfg = cls()
        for f in fields(cls):
            if f.name.startswith("_"):
                continue
            key = f.name.upper()
            if key not in env or env[key] == "":
                continue
            raw = env[key]
            current = getattr(cfg, f.name)
            try:
                if isinstance(current, bool):
                    val = _parse_bool(raw)
                elif isinstance(current, int):
                    val = int(raw)
                elif isinstance(current, float):
                    val = float(raw)
                elif isinstance(current, time):
                    val = _parse_time(raw)
                elif isinstance(current, Path):
                    val = Path(raw)
                else:
                    val = raw
            except ValueError as exc:
                raise ConfigError(f"Ugyldig værdi for {key}: {raw!r}") from exc
            setattr(cfg, f.name, val)
            cfg._from_env.add(f.name)
        cfg.validate(env)
        return cfg

    def summary(self) -> str:
        skip = {"telegram_token", "_from_env"}
        parts = []
        for f in fields(self):
            if f.name in skip:
                continue
            parts.append(f"{f.name}={getattr(self, f.name)}")
        return ", ".join(parts)


def read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        out[k] = v.strip().strip('"').strip("'")
    return out


def replace(cfg: Config, **changes) -> Config:
    return dataclasses.replace(cfg, **changes)

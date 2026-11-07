"""Strategimotoren: opening range breakout på XSP 0DTE.

Motoren kender kun ``Broker``-interfacet og en ``now()``-funktion, så hele
dagsforløbet kan testes med en falsk broker og et falsk ur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Callable, Optional

from .broker import Broker, OptionPosition
from .calendar import at, half_day_list_is_stale, is_half_day, is_weekday, now_et
from .config import Config
from .notify import Notifier
from .pricing import (
    breakout_signal,
    entry_filter,
    exit_reason,
    pick_strike,
    pnl_usd,
    position_size,
    round_to_tick,
)
from .state import StateStore
from .tradelog import append_trade

log = logging.getLogger("xspbot.engine")


@dataclass
class Holding:
    contract: Any
    qty: int
    entry: float  # pris pr. aktie (fx 1.25)
    description: str
    adopted: bool = False


class Bot:
    def __init__(
        self,
        cfg: Config,
        broker: Broker,
        notifier: Notifier,
        store: StateStore,
        now: Callable[[], datetime] = now_et,
    ):
        self.cfg = cfg
        self.broker = broker
        self.notifier = notifier
        self.store = store
        self.now = now
        self.tag = "[DRY RUN] " if cfg.dry_run else ""

    # ------------------------------------------------------------------
    # Hjælpere
    def notify(self, text: str) -> None:
        self.notifier.send(f"{self.tag}{text}")

    def alert(self, text: str) -> None:
        self.notifier.error(f"{self.tag}{text}")

    def stop_requested(self) -> bool:
        return self.cfg.stop_path.exists()

    def today_at(self, t: time) -> datetime:
        return at(self.now().date(), t)

    def market_open_now(self) -> bool:
        n = self.now()
        return self.today_at(self.cfg.range_start) <= n < self.today_at(self.cfg.market_close)

    def wait_until(self, t: time) -> bool:
        """Vent til klokken t (ET). Returnér False hvis STOP-filen dukker op."""
        target = self.today_at(t)
        while self.now() < target:
            if self.stop_requested():
                return False
            remaining = (target - self.now()).total_seconds()
            self.broker.sleep(max(0.0, min(remaining, 1.0)))
        return True

    # ------------------------------------------------------------------
    def run(self) -> str:
        """Kør én handelsdag. Returnerer et kort udfald (til log/test)."""
        cfg = self.cfg
        today = self.now().date()
        iso = today.isoformat()
        log.info("Start %s  (%s, port %s, dry_run=%s)", iso, "LIVE" if cfg.is_live else "PAPER", cfg.ib_port, cfg.dry_run)

        self.broker.connect()

        # 1) Oprydning og overtagelse af evt. åbne positioner - ALTID først,
        #    også når målet er nået eller STOP-filen findes.
        if not cfg.dry_run:
            n = self.broker.cancel_open_orders()
            if n:
                self.alert(f"Annullerede {n} efterladte XSP-ordre(r) ved opstart")
        positions = self.broker.option_positions()
        if positions:
            # Der har været en handel i dag (eller tidligere) - ingen ny entry.
            self.store.mark_trade_date(iso)
            holdings = [self._adopt(p) for p in positions]
            self.manage_exit(holdings)
            self.check_target()
            return "adopted"

        state = self.store.load()
        if state.done:
            log.info("Målet er nået (%s). Botten er stoppet permanent, indtil state nulstilles.", state.done_at)
            return "done"
        if self.stop_requested():
            self.notify("STOP-fil fundet - ingen nye handler")
            return "stop"
        if not is_weekday(today):
            return "weekend"
        if half_day_list_is_stale(today):
            self.alert("Listen over halve handelsdage dækker ikke i år - opdatér xspbot/calendar.py")
        if is_half_day(today):
            self.notify(f"Springer over: {iso} er en halv handelsdag")
            return "half_day"
        if state.last_trade_date == iso:
            log.info("Der er allerede handlet i dag (%s) - ingen ny handel", iso)
            return "already_traded"

        cash = self.broker.cash_usd()
        log.info("Disponibel USD-kontant: %.2f", cash)
        if self.check_target(cash):
            return "done"

        expiry = today.strftime("%Y%m%d")
        expirations, strikes = self.broker.chain(today)
        if expiry not in expirations or not strikes:
            self.notify(f"Springer over: intet XSP-udløb i dag ({iso}) - helligdag?")
            return "no_expiry"

        # 2) Opening range
        if not self.wait_until(cfg.range_end):
            self.notify("STOP-fil fundet - ingen nye handler")
            return "stop"
        if self.now() >= self.today_at(cfg.entry_deadline):
            self.notify("Springer over: startet efter entry-deadline")
            return "too_late"
        orng = self.broker.opening_range(today, cfg.range_start, cfg.range_end)
        if orng is None:
            self.alert("Kunne ikke hente opening range - ingen handel i dag")
            return "no_range"
        high, low = orng
        self.notify(
            f"Opening range {iso}: high {high:.2f} / low {low:.2f}. "
            f"Køb CALL over {high + cfg.breakout_buffer:.2f}, PUT under {low - cfg.breakout_buffer:.2f}"
        )

        # 3) Overvåg breakout
        sig = self.watch_for_breakout(high, low)
        if sig is None:
            return "no_signal"
        if sig == "stop":
            return "stop"
        right, price = sig
        self.notify(f"Signal: {'CALL' if right == 'C' else 'PUT'} - XSP {price:.2f} brød {'high' if right == 'C' else 'low'}")

        # 4) Entry
        holding = self.enter(right, price, strikes, expiry, cash)
        if holding is None:
            return "skipped"

        # 5) Exit
        self.manage_exit([holding])
        self.check_target()
        return "traded"

    # ------------------------------------------------------------------
    def _adopt(self, p: OptionPosition) -> Holding:
        entry = p.avg_cost / self.cfg.multiplier
        h = Holding(p.contract, p.qty, entry, p.description, adopted=True)
        if p.qty < 0:
            self.alert(f"Overtaget KORT position {p.description} x{p.qty} - håndtér manuelt!")
        self.notify(f"Overtager åben position: {p.description} x{p.qty}, entry {entry:.2f}")
        return h

    def watch_for_breakout(self, high: float, low: float):
        cfg = self.cfg
        deadline = self.today_at(cfg.entry_deadline)
        last_ok = self.now()
        alerted = False
        while self.now() < deadline:
            if self.stop_requested():
                self.notify("STOP-fil fundet - ingen nye handler")
                return "stop"
            price = self.broker.index_price()
            if price is None:
                if not alerted and (self.now() - last_ok).total_seconds() > cfg.data_stale_alert_sec:
                    self.alert("Ingen XSP-kurs i over 60 sek. - tjek markedsdata")
                    alerted = True
            else:
                last_ok = self.now()
                alerted = False
                right = breakout_signal(price, high, low, cfg.breakout_buffer)
                if right:
                    return right, price
            self.broker.sleep(cfg.poll_interval_sec)
        self.notify("Springer over: intet breakout før 13:30 ET")
        return None

    def enter(self, right: str, price: float, strikes, expiry: str, cash: float) -> Optional[Holding]:
        cfg = self.cfg
        iso = self.now().date().isoformat()
        strike = pick_strike(strikes, price, right)
        if strike is None:
            self.notify("Springer over: ingen passende strike")
            return None
        contract = self.broker.option(expiry, strike, right)
        desc = self.broker.describe(contract)
        q = self.broker.quote(contract)
        why = entry_filter(q, cfg.min_mid, cfg.max_spread_pct)
        if why:
            self.notify(f"Springer over {desc}: {why}")
            return None
        limit = round_to_tick(q.ask, "up")
        cash = self.broker.cash_usd()
        qty = position_size(cash, limit, cfg.fee_per_contract, cfg.multiplier)
        if qty < 1:
            self.notify(
                f"Springer over {desc}: ikke nok kontanter (${cash:.2f}) til én kontrakt à "
                f"${limit * cfg.multiplier + cfg.fee_per_contract:.2f}"
            )
            return None

        # Handelsdatoen gemmes FØR ordren sendes: en genstart må aldrig
        # handle to gange samme dag.
        self.store.mark_trade_date(iso)

        if cfg.dry_run:
            filled, avg = qty, limit
        else:
            trade = self.broker.place_limit(contract, "BUY", qty, limit)
            st = self._wait_fill(trade, cfg.entry_timeout_sec)
            if not st.done:
                self.broker.cancel(trade)
                st = self.broker.order_status(trade)
            filled, avg = st.filled, st.avg_price or limit
            if filled == 0:
                if st.done:
                    self.store.clear_trade_date(iso)
                    self.notify(f"Køb af {desc} ikke fyldt på {limit:.2f} inden {cfg.entry_timeout_sec:.0f} sek. - annulleret")
                    return None
                # Annullering ikke bekræftet: tjek om der alligevel er en position
                self.broker.sleep(2)
                for p in self.broker.option_positions():
                    if self.broker.describe(p.contract) == desc:
                        self.alert(f"Købsordre blev fyldt efter annullering - overtager {desc}")
                        return self._adopt(p)
                self.alert(f"Købsordre for {desc} kunne ikke bekræftes annulleret - tjek TWS manuelt!")
                return None
            if filled < qty:
                self.notify(f"Delvist fyldt: {filled}/{qty}")
        self.notify(
            f"KØB {desc} x{filled} @ {avg:.2f} (kostede ca. ${avg * cfg.multiplier * filled + cfg.fee_per_contract * filled:.2f})"
        )
        return Holding(contract, filled, avg, desc)

    def _wait_fill(self, handle, timeout: float):
        waited = 0.0
        st = self.broker.order_status(handle)
        while not st.done and waited < timeout:
            self.broker.sleep(1.0)
            waited += 1.0
            st = self.broker.order_status(handle)
        return st

    # ------------------------------------------------------------------
    def manage_exit(self, holdings: list[Holding]) -> None:
        cfg = self.cfg
        open_ = [h for h in holdings if h.qty > 0]
        for h in holdings:
            if h.qty <= 0:
                self.alert(f"{h.description}: kort/nul position ({h.qty}) styres ikke automatisk")
        if not open_:
            return
        # Vent på markedsåbning hvis vi er startet før 09:30
        if self.now() < self.today_at(cfg.range_start):
            log.info("Venter på markedsåbning for at styre position(er)")
            while self.now() < self.today_at(cfg.range_start):
                self.broker.sleep(1.0)
        if self.now() >= self.today_at(cfg.market_close):
            self.alert(
                "Markedet er lukket, men der findes åbne XSP-positioner: "
                + ", ".join(h.description for h in open_)
                + " - håndtér manuelt"
            )
            return

        last_ok = {id(h): self.now() for h in open_}
        alerted: set[int] = set()
        log.info("Overvåger exit for: %s", ", ".join(f"{h.description} x{h.qty} @ {h.entry:.2f}" for h in open_))
        while open_:
            stop = self.stop_requested()
            time_up = self.now() >= self.today_at(cfg.time_stop)
            for h in list(open_):
                q = self.broker.quote(h.contract)
                mid = q.mid
                if mid is None:
                    if id(h) not in alerted and (self.now() - last_ok[id(h)]).total_seconds() > cfg.data_stale_alert_sec:
                        self.alert(f"Ingen kurs på {h.description} i over 60 sek. - positionen overvåges stadig")
                        alerted.add(id(h))
                else:
                    last_ok[id(h)] = self.now()
                    alerted.discard(id(h))
                reason = exit_reason(mid, h.entry, cfg.take_profit_pct, cfg.stop_loss_pct, time_up, stop)
                if reason:
                    log.info("Exit %s: %s (mid=%s, entry=%.2f)", h.description, reason, mid, h.entry)
                    self.exit_position(h, reason)
                    open_.remove(h)
            if open_:
                self.broker.sleep(cfg.poll_interval_sec)

    def exit_position(self, h: Holding, reason: str) -> None:
        cfg = self.cfg
        remaining = h.qty
        sold = 0
        proceeds = 0.0
        attempt = 0
        warned_no_bid = False
        while remaining > 0:
            if self.now() >= self.today_at(cfg.market_close):
                self.alert(
                    f"Markedet er lukket - {remaining} stk. {h.description} blev IKKE solgt. "
                    "XSP er kontantafregnet ved udløb. Tjek kontoen manuelt!"
                )
                break
            attempt += 1
            q = self.broker.quote(h.contract)
            if not q.has_bid:
                if not warned_no_bid:
                    self.alert(f"Ingen bid på {h.description} - kan ikke sælge endnu (forsøg {attempt})")
                    warned_no_bid = True
                self.broker.sleep(cfg.exit_timeout_sec)
            else:
                price = round_to_tick(q.bid, "down")
                if cfg.dry_run:
                    filled, avg = remaining, price
                else:
                    trade = self.broker.place_limit(h.contract, "SELL", remaining, price)
                    st = self._wait_fill(trade, cfg.exit_timeout_sec)
                    if not st.done:
                        self.broker.cancel(trade)
                        st = self.broker.order_status(trade)
                    filled, avg = st.filled, st.avg_price or price
                if filled:
                    sold += filled
                    proceeds += filled * avg
                    remaining -= filled
            if remaining > 0 and attempt % cfg.exit_max_attempts == 0:
                self.alert(
                    f"{h.description}: {remaining} stk. ikke solgt efter {attempt} forsøg ({reason}). "
                    "Fortsætter med at forsøge - overvej manuel handling."
                )
                warned_no_bid = False

        exit_price = proceeds / sold if sold else 0.0
        pl = pnl_usd(h.entry, exit_price, sold, cfg.fee_per_contract, cfg.multiplier) if sold else 0.0
        if remaining > 0:
            # Usolgte kontrakter: worst case værdi 0
            pl += -h.entry * cfg.multiplier * remaining - cfg.fee_per_contract * remaining
        row = {
            "dato": self.now().date().isoformat(),
            "kontrakt": h.description,
            "antal": h.qty,
            "entry": f"{h.entry:.2f}",
            "exit": f"{exit_price:.2f}",
            "aarsag": reason + (f" (usolgt: {remaining})" if remaining else ""),
            "pl_usd": f"{pl:.2f}",
            "dry_run": int(cfg.dry_run),
        }
        try:
            append_trade(cfg.trades_path, row)
        except Exception:
            log.exception("Kunne ikke skrive trades.csv")
        self.notify(
            f"SALG {h.description} x{sold} @ {exit_price:.2f} ({reason}). P/L ca. ${pl:+.2f}"
        )

    # ------------------------------------------------------------------
    def check_target(self, cash: Optional[float] = None) -> bool:
        cfg = self.cfg
        if cfg.dry_run:
            return False
        try:
            if cash is None:
                self.broker.sleep(3)  # lad kontoværdier opdatere efter salg
                cash = self.broker.cash_usd()
        except Exception:
            log.exception("Kunne ikke hente saldo til måltjek")
            return False
        if cash >= cfg.target_usd:
            self.store.mark_done(f"saldo ${cash:.2f} >= mål ${cfg.target_usd:.2f}", self.now().isoformat())
            self.notify(
                f"🎯 MÅL NÅET: saldo ${cash:.2f} >= ${cfg.target_usd:.2f}. "
                "Botten er stoppet permanent - nulstil med: python -m xspbot --reset-target"
            )
            return True
        return False

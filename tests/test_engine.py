"""Dagsforløb end-to-end med falsk broker og falsk ur."""

import csv
from datetime import datetime, time

import pytest

from tests.conftest import FakeClock, FakeContract
from xspbot.broker import OptionPosition
from xspbot.calendar import ET
from xspbot.pricing import Quote


def at(h, m, s=0):
    return datetime(2026, 11, 10, h, m, s, tzinfo=ET)


def breakout_up_at(when, before=679.0, after=681.0):
    return lambda t: after if t >= when else before


def rows(cfg):
    if not cfg.trades_path.exists():
        return []
    with cfg.trades_path.open() as fh:
        return list(csv.DictReader(fh))


def buys(broker):
    return [o for o in broker.orders if o.action == "BUY"]


def sells(broker):
    return [o for o in broker.orders if o.action == "SELL"]


class TestFullDay:
    def test_call_take_profit(self, make_bot, broker, store, cfg, clock, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        assert make_bot().run() == "traded"

        (b,) = buys(broker)
        assert b.contract.right == "C" and b.contract.strike == 682.0  # første strike over 681
        assert b.qty == 1 and b.price == 1.05  # floor(150 / 106.10) = 1
        (s,) = sells(broker)
        assert s.price == 2.10 and s.qty == 1
        assert clock.now() < at(11, 1)
        (r,) = rows(cfg)
        assert r["aarsag"] == "take profit" and r["entry"] == "1.05" and r["exit"] == "2.10"
        assert float(r["pl_usd"]) == pytest.approx((2.10 - 1.05) * 100 - 2.2)
        assert r["dry_run"] == "0"
        assert store.load().last_trade_date == "2026-11-10"
        t = notifier.text()
        for word in ("Opening range", "Signal: CALL", "KØB", "SALG"):
            assert word in t

    def test_put_stop_loss(self, make_bot, broker, cfg):
        broker.index_fn = lambda t: 677.5 if t >= at(11, 0) else 679.0
        broker.quote_fn = lambda c, t: Quote(0.45, 0.48) if t >= at(11, 30) else Quote(1.00, 1.05)
        make_bot().run()
        (b,) = buys(broker)
        assert b.contract.right == "P" and b.contract.strike == 677.0
        assert rows(cfg)[0]["aarsag"] == "stop loss"
        assert sells(broker)[0].price == 0.45

    def test_time_stop(self, make_bot, broker, cfg, clock):
        broker.index_fn = breakout_up_at(at(10, 5))
        broker.quote_fn = lambda c, t: Quote(1.20, 1.25) if t >= at(10, 6) else Quote(1.00, 1.05)
        make_bot().run()
        assert rows(cfg)[0]["aarsag"] == "tidsstop"
        assert at(15, 0) <= sells(broker)[0].placed_at < at(15, 0, 3)

    def test_no_signal(self, make_bot, broker, notifier, clock):
        assert make_bot().run() == "no_signal"
        assert broker.orders == []
        assert clock.now() >= at(13, 30)
        assert "intet breakout" in notifier.text()

    def test_signal_within_buffer_ignored(self, make_bot, broker):
        broker.index_fn = lambda t: 680.25  # high 680 + 0.25 er ikke > 
        assert make_bot().run() == "no_signal"


class TestSkips:
    def test_wide_spread(self, make_bot, broker, store, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(0.80, 1.20)
        assert make_bot().run() == "skipped"
        assert broker.orders == [] and store.load().last_trade_date is None
        assert "spread" in notifier.text()

    def test_mid_too_low(self, make_bot, broker):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(0.10, 0.11)
        assert make_bot().run() == "skipped"

    def test_not_enough_cash(self, make_bot, broker, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.40, 2.45)
        assert make_bot().run() == "skipped"
        assert broker.orders == []
        assert "ikke nok kontanter" in notifier.text()

    def test_no_expiry_today(self, make_bot, broker, notifier):
        broker.expirations = {"20261111"}
        assert make_bot().run() == "no_expiry"
        assert "intet XSP-udløb" in notifier.text()

    def test_half_day(self, make_bot, broker, clock):
        clock.t = datetime(2026, 11, 27, 9, 15, tzinfo=ET)
        assert make_bot().run() == "half_day"

    def test_weekend(self, make_bot, clock):
        clock.t = datetime(2026, 11, 14, 9, 15, tzinfo=ET)
        assert make_bot().run() == "weekend"

    def test_already_traded_today(self, make_bot, broker, store):
        store.mark_trade_date("2026-11-10")
        broker.index_fn = breakout_up_at(at(10, 30))
        assert make_bot().run() == "already_traded"
        assert broker.orders == []


class TestEntryOrders:
    def test_entry_not_filled_cancelled_after_20s(self, make_bot, broker, store, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.fill_after = lambda action, n: None
        assert make_bot().run() == "skipped"
        (b,) = buys(broker)
        assert b.cancelled
        assert store.load().last_trade_date is None
        assert "ikke fyldt" in notifier.text()

    def test_trade_date_saved_before_order(self, make_bot, broker, store):
        broker.index_fn = breakout_up_at(at(10, 30))
        seen = {}
        orig = broker.place_limit

        def spy(contract, action, qty, price):
            seen.setdefault(action, store.load().last_trade_date)
            return orig(contract, action, qty, price)

        broker.place_limit = spy
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        make_bot().run()
        assert seen == {"BUY": "2026-11-10", "SELL": "2026-11-10"}

    def test_buy_rounds_ask_up_to_tick(self, make_bot, broker):
        broker.cash = 1000
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(3.00, 3.12) if t < at(10, 31) else Quote(6.5, 6.6)
        make_bot().run()
        b = buys(broker)[0]
        assert b.price == 3.15
        assert b.qty == 1000 // (315 + 1.1)


class TestExitOrders:
    def test_sell_retries_with_new_bid(self, make_bot, broker, cfg):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        broker.fill_after = lambda action, n: 0.0 if action == "BUY" or n >= 3 else None
        make_bot().run()
        s = sells(broker)
        assert len(s) == 4
        assert all(o.cancelled for o in s[:3]) and s[3].filled == 1
        # hvert forsøg venter 8 sek.
        assert 8 <= (s[1].placed_at - s[0].placed_at).total_seconds() <= 10
        assert rows(cfg)[0]["exit"] == "2.10"

    def test_alert_after_8_failed_attempts(self, make_bot, broker, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        broker.fill_after = lambda action, n: 0.0 if action == "BUY" or n >= 9 else None
        make_bot().run()
        assert len(sells(broker)) == 10
        assert "ikke solgt efter 8 forsøg" in notifier.text()

    def test_no_bid_warns_then_sells(self, make_bot, broker, notifier, cfg):
        broker.index_fn = breakout_up_at(at(10, 30))

        def q(c, t):
            if t < at(10, 31):
                return Quote(1.00, 1.05)
            if t < at(14, 59):
                return Quote(0.90, 1.00)
            if t < at(15, 0, 30):
                return Quote(None, 0.05)  # ingen bid
            return Quote(0.02, 0.05)

        broker.quote_fn = q
        make_bot().run()
        assert "Ingen bid" in notifier.text()
        (r,) = rows(cfg)
        assert r["exit"] == "0.02"

    def test_partial_entry_fill(self, make_bot, broker, cfg):
        broker.cash = 1000
        broker.index_fn = breakout_up_at(at(10, 30))
        orig = broker.place_limit

        def partial(contract, action, qty, price):
            o = orig(contract, action, qty, price)
            if action == "BUY":
                o.fill_after = None
                o.filled = 3
                broker._book(o)
            return o

        broker.place_limit = partial
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        make_bot().run()
        assert buys(broker)[0].qty == 9
        assert sells(broker)[0].qty == 3
        assert rows(cfg)[0]["antal"] == "3"


class TestRecovery:
    def _existing(self, broker, avg_cost=120.0, qty=1):
        c = FakeContract("XSP", "20261110", 682.0, "C", conId=6820, localSymbol="XSP 682C")
        broker.positions = [OptionPosition(c, qty, avg_cost, "XSP 682C", "20261110")]
        return c

    def test_adopt_open_position_uses_avgcost(self, make_bot, broker, store, cfg, clock):
        clock.t = at(11, 0)
        self._existing(broker, avg_cost=120.0)
        broker.quote_fn = lambda c, t: Quote(2.40, 2.45) if t >= at(11, 5) else Quote(1.30, 1.35)
        assert make_bot().run() == "adopted"
        assert buys(broker) == []
        (r,) = rows(cfg)
        assert r["entry"] == "1.20" and r["aarsag"] == "take profit"
        assert store.load().last_trade_date == "2026-11-10"
        assert broker.cancel_open_calls == 1

    def test_adopt_even_when_target_done(self, make_bot, broker, store, clock):
        store.mark_done("test", "x")
        clock.t = at(14, 59, 50)
        self._existing(broker)
        broker.quote_fn = lambda c, t: Quote(1.0, 1.1)
        assert make_bot().run() == "adopted"
        assert len(sells(broker)) == 1

    def test_crash_during_exit_then_restart_adopts(self, make_bot, broker, store, cfg, clock):
        broker.index_fn = breakout_up_at(at(10, 30))
        state = {"crash": True}

        def q(c, t):
            if t >= at(10, 40) and state["crash"]:
                state["crash"] = False
                raise ConnectionError("Gateway forsvandt")
            return Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)

        broker.quote_fn = q
        with pytest.raises(ConnectionError):
            make_bot().run()
        assert store.load().last_trade_date == "2026-11-10"
        assert len(broker.positions) == 1
        assert make_bot().run() == "adopted"
        assert len(buys(broker)) == 1 and len(sells(broker)) == 1
        assert broker.positions == []


class TestStopFile:
    def test_stop_before_trading(self, make_bot, broker, cfg):
        cfg.stop_path.touch()
        assert make_bot().run() == "stop"
        assert broker.orders == []

    def test_stop_appears_while_waiting(self, make_bot, broker, cfg, clock):
        broker.on_sleep = lambda: clock.now() >= at(9, 45) and cfg.stop_path.touch()
        assert make_bot().run() == "stop"
        assert broker.orders == []

    def test_stop_with_open_position_closes_it(self, make_bot, broker, cfg, clock):
        clock.t = at(11, 0)
        c = FakeContract("XSP", "20261110", 682.0, "C", conId=6820, localSymbol="XSP 682C")
        broker.positions = [OptionPosition(c, 2, 100.0, "XSP 682C", "20261110")]
        cfg.stop_path.touch()
        assert make_bot().run() == "adopted"
        (s,) = sells(broker)
        assert s.qty == 2
        assert rows(cfg)[0]["aarsag"] == "nødstop"

    def test_stop_during_exit(self, make_bot, broker, cfg, clock):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.on_sleep = lambda: clock.now() >= at(12, 0) and cfg.stop_path.touch()
        make_bot().run()
        assert rows(cfg)[0]["aarsag"] == "nødstop"
        assert at(12, 0) <= sells(broker)[0].placed_at < at(12, 0, 3)


class TestTargetAndDryRun:
    def test_target_reached_sets_permanent_flag(self, make_bot, broker, store, notifier):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        orig = broker.place_limit

        def sell_hook(contract, action, qty, price):
            if action == "SELL":
                broker.cash = 1500.0
            return orig(contract, action, qty, price)

        broker.place_limit = sell_hook
        make_bot().run()
        assert store.load().done is True
        assert "MÅL NÅET" in notifier.text()
        n = len(broker.orders)
        assert make_bot().run() == "done"
        assert len(broker.orders) == n

    def test_target_already_reached_at_start(self, make_bot, broker, store):
        broker.cash = 1460
        assert make_bot().run() == "done"
        assert store.load().done

    def test_dry_run_places_no_orders(self, make_bot, broker, cfg, store):
        broker.index_fn = breakout_up_at(at(10, 30))
        broker.quote_fn = lambda c, t: Quote(2.10, 2.20) if t >= at(11, 0) else Quote(1.00, 1.05)
        assert make_bot(dry_run=True).run() == "traded"
        assert broker.orders == []
        (r,) = rows(cfg)
        assert r["dry_run"] == "1" and r["aarsag"] == "take profit"
        assert store.load().done is False

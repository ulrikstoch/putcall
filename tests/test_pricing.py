import pytest

from xspbot.pricing import (
    Quote,
    breakout_signal,
    entry_filter,
    exit_reason,
    pick_strike,
    pnl_usd,
    position_size,
    round_to_tick,
    tick_size,
)


class TestTicks:
    def test_tick_size(self):
        assert tick_size(0.15) == 0.01
        assert tick_size(2.99) == 0.01
        assert tick_size(3.00) == 0.05
        assert tick_size(12.3) == 0.05

    @pytest.mark.parametrize(
        "price,direction,expected",
        [
            (1.234, "up", 1.24),
            (1.234, "down", 1.23),
            (1.235, "nearest", 1.24),
            (1.20, "up", 1.20),  # allerede gyldig
            (0.1 + 0.2, "up", 0.30),  # float-støj
            (3.01, "up", 3.05),
            (3.01, "down", 3.00),
            (3.07, "nearest", 3.05),
            (3.08, "nearest", 3.10),
            (2.999, "up", 3.00),  # krydser $3-grænsen
            (12.34, "down", 12.30),
            (0.004, "down", 0.01),  # aldrig 0
        ],
    )
    def test_round_to_tick(self, price, direction, expected):
        assert round_to_tick(price, direction) == pytest.approx(expected)

    def test_result_is_valid_tick(self):
        for cents in range(1, 1000):
            p = cents / 100 + 0.003
            for d in ("up", "down", "nearest"):
                r = round_to_tick(p, d)
                tick = 0.01 if r < 3 else 0.05
                assert abs(r / tick - round(r / tick)) < 1e-6, (p, d, r)

    @pytest.mark.parametrize("bad", [0, -1, None, float("nan")])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            round_to_tick(bad)


class TestStrike:
    strikes = [675.0, 676.0, 677.0, 678.0, 679.0, 680.0]

    def test_call_first_above(self):
        assert pick_strike(self.strikes, 677.3, "C") == 678.0

    def test_call_exactly_on_strike_goes_above(self):
        assert pick_strike(self.strikes, 677.0, "C") == 678.0

    def test_put_first_below(self):
        assert pick_strike(self.strikes, 677.3, "P") == 677.0

    def test_put_exactly_on_strike_goes_below(self):
        assert pick_strike(self.strikes, 677.0, "P") == 676.0

    def test_unsorted_and_duplicates(self):
        assert pick_strike([680, 676, 678, 678, 677], 676.5, "C") == 677.0

    def test_none_available(self):
        assert pick_strike(self.strikes, 681, "C") is None
        assert pick_strike(self.strikes, 674, "P") is None

    def test_bad_right(self):
        with pytest.raises(ValueError):
            pick_strike(self.strikes, 677, "X")


class TestSize:
    def test_basic(self):
        # 150 / (1.20*100 + 1.10) = 1.238 -> 1
        assert position_size(150, 1.20, 1.10) == 1

    def test_multiple(self):
        # 1000 / (0.50*100 + 1.10) = 19.57 -> 19
        assert position_size(1000, 0.50, 1.10) == 19

    def test_exact_boundary(self):
        assert position_size(121.10, 1.20, 1.10) == 1
        assert position_size(121.09, 1.20, 1.10) == 0

    def test_too_expensive(self):
        assert position_size(150, 2.50, 1.10) == 0

    def test_zero_cash(self):
        assert position_size(0, 1.0, 1.1) == 0
        assert position_size(-5, 1.0, 1.1) == 0


class TestFilters:
    def test_ok(self):
        assert entry_filter(Quote(1.00, 1.10), 0.15, 0.20) is None

    def test_mid_too_low(self):
        assert "mid" in entry_filter(Quote(0.10, 0.12), 0.15, 0.20)

    def test_mid_exactly_min_ok(self):
        assert entry_filter(Quote(0.14, 0.16), 0.15, 0.20) is None

    def test_spread_too_wide(self):
        assert "spread" in entry_filter(Quote(0.80, 1.20), 0.15, 0.20)

    def test_spread_exactly_20pct_ok(self):
        assert entry_filter(Quote(0.90, 1.10), 0.15, 0.20) is None

    def test_no_bid(self):
        assert entry_filter(Quote(None, 1.0), 0.15, 0.20) is not None


class TestSignal:
    def test_breakouts(self):
        assert breakout_signal(680.26, 680, 678, 0.25) == "C"
        assert breakout_signal(680.25, 680, 678, 0.25) is None
        assert breakout_signal(677.74, 680, 678, 0.25) == "P"
        assert breakout_signal(677.75, 680, 678, 0.25) is None
        assert breakout_signal(None, 680, 678, 0.25) is None


class TestExit:
    def test_take_profit(self):
        assert exit_reason(2.00, 1.00, 1.0, 0.5, False) == "take profit"
        assert exit_reason(1.99, 1.00, 1.0, 0.5, False) is None

    def test_stop_loss(self):
        assert exit_reason(0.50, 1.00, 1.0, 0.5, False) == "stop loss"
        assert exit_reason(0.51, 1.00, 1.0, 0.5, False) is None

    def test_time_stop(self):
        assert exit_reason(1.10, 1.00, 1.0, 0.5, True) == "tidsstop"

    def test_tp_beats_time(self):
        assert exit_reason(2.5, 1.00, 1.0, 0.5, True) == "take profit"

    def test_stop_file_first(self):
        assert exit_reason(2.5, 1.00, 1.0, 0.5, True, stop_file=True) == "nødstop"

    def test_no_mid_only_time(self):
        assert exit_reason(None, 1.00, 1.0, 0.5, False) is None
        assert exit_reason(None, 1.00, 1.0, 0.5, True) == "tidsstop"

    def test_mid_without_bid(self):
        assert Quote(None, 0.10).mid == pytest.approx(0.05)
        assert Quote(None, None).mid is None

    def test_pnl(self):
        assert pnl_usd(1.00, 2.00, 1, 1.10) == pytest.approx(97.80)
        assert pnl_usd(1.00, 0.50, 2, 1.10) == pytest.approx(-104.40)


class TestBacktestHelpers:
    def test_bs_put_call_parity_and_intrinsic(self):
        import math

        from backtest import bs_price, longest_losing_streak

        c = bs_price(700, 701, 120, 0.16, "C", r=0.0)
        p = bs_price(700, 701, 120, 0.16, "P", r=0.0)
        assert c - p == pytest.approx(700 - 701, abs=1e-9)
        assert bs_price(705, 700, 0, 0.2, "C") == 5
        assert bs_price(705, 700, 0, 0.2, "P") == 0
        assert longest_losing_streak([1, -1, -2, 3, -1, -1, -1, 2]) == 3

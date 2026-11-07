"""Tests for tradelab: motor, look-ahead, nøgletal, register, gates, godkendelse og executor."""

import json
from datetime import date
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from tradelab import engine, gates, lab, metrics
from tradelab import strategies as S
from tradelab.approvals import Approvals
from tradelab.data import Prices, completed_sessions_only
from tradelab.engine import Costs
from tradelab.executor import Refused, check_mode, plan_orders, rebalance, select_candidate
from tradelab.registry import Candidate, Registry, candidate_hash

SYMS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "BIL"]


def synthetic(days=252 * 12, seed=0, drift=0.0004) -> Prices:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2005-01-03", periods=days)
    close = {}
    for i, s in enumerate(SYMS):
        r = rng.normal(drift if s != "BIL" else 0.0001, 0.012 if s != "BIL" else 0.0005, days)
        close[s] = 100 * np.exp(np.cumsum(r)) * (1 + i / 10)
    c = pd.DataFrame(close, index=idx)
    o = c.shift(1).fillna(c.iloc[0]) * (1 + rng.normal(0, 0.001, c.shape))
    return Prices(o, c)


@pytest.fixture(scope="module")
def prices():
    return synthetic()


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "registry.json", tmp_path / "trials.jsonl")


class LookAheadCheat(S.Strategy):
    name, version, defaults = "Cheat", 1, {"symbol": "SPY"}

    @classmethod
    def universe(cls, p):
        return [p["symbol"]]

    @classmethod
    def weights(cls, close, p):
        w = S._frame(close)
        w[p["symbol"]] = (close[p["symbol"]].shift(-1) > close[p["symbol"]]).astype(float)
        return w


class TestEngine:
    def test_buy_hold_matches_price_minus_costs(self, prices):
        c = Costs(slippage_bps=0)
        r = engine.run(S.BuyHold, {"symbol": "SPY"}, prices, start=prices.close.index[10], capital=1000, costs=c)
        px = prices.close["SPY"]
        buy_open = prices.open["SPY"].iloc[10]
        expected = (1000 - c.commission(1, buy_open)) / buy_open * px.iloc[-1]
        assert r.equity.iloc[-1] == pytest.approx(expected, rel=2e-3)
        assert len(r.trades) == 1

    def test_signal_executes_next_day_open(self, prices):
        r = engine.run(S.TrendSMA, S.TrendSMA.params(), prices, capital=1000)
        w = r.weights["SPY"]
        first_on = w[w > 0].index[0]
        t = r.trades[r.trades.symbol == "SPY"].iloc[0]
        assert t.date == prices.close.index[prices.close.index.get_loc(first_on) + 1]

    def test_min_commission_hurts_small_accounts(self, prices):
        p = S.RSI2MeanReversion.params()
        small = engine.run(S.RSI2MeanReversion, p, prices, capital=150)
        big = engine.run(S.RSI2MeanReversion, p, prices, capital=100_000)
        assert small.costs_paid / 150 > big.costs_paid / 100_000 * 5

    def test_commission_rules(self):
        c = Costs()
        assert c.commission(1, 100) == pytest.approx(0.35)  # minimum
        assert c.commission(0.1, 100) == pytest.approx(0.10)  # max 1 % af $10
        assert c.commission(1000, 50) == pytest.approx(3.5)

    def test_never_negative_cash_and_weights_sum(self, prices):
        r = engine.run(S.DualMomentum, S.DualMomentum.params({"top": 3}), prices, capital=150)
        assert (r.equity > 0).all()
        assert (r.weights.sum(axis=1) <= 1 + 1e-9).all()


class TestLookAhead:
    @pytest.mark.parametrize("key", ["BuyHold_v1", "TrendSMA_v1", "DualMomentum_v1", "RSI2MeanReversion_v1"])
    def test_builtin_strategies_clean(self, prices, key):
        st = S.get(key)
        assert engine.lookahead_check(st, st.params(), prices) == []

    def test_cheat_is_caught(self, prices):
        assert engine.lookahead_check(LookAheadCheat, {"symbol": "SPY"}, prices)


class TestMetrics:
    def test_sharpe_and_drawdown(self):
        eq = pd.Series([100, 110, 99, 120], index=pd.bdate_range("2020-01-01", periods=4))
        assert metrics.max_drawdown(eq) == pytest.approx(-0.1)
        assert metrics.sharpe(pd.Series([0.01] * 10)) == 0.0

    def test_expected_max_sharpe_grows_with_trials(self):
        assert metrics.expected_max_sharpe(1, 0.25) == 0
        assert 0 < metrics.expected_max_sharpe(10, 0.25) < metrics.expected_max_sharpe(1000, 0.25)

    def test_dsr_penalises_many_trials(self):
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.0004, 0.01, 252 * 10))
        few = metrics.deflated_sharpe(r, 1, [])
        many = metrics.deflated_sharpe(r, 500, list(rng.normal(0, 0.5, 500)))
        assert many < few

    def test_psr_direction(self):
        rng = np.random.default_rng(2)
        noise = rng.normal(0, 0.01, 2520)
        assert metrics.psr(pd.Series(noise + 0.001)) > 0.95
        assert metrics.psr(pd.Series(noise - 0.001)) < 0.05


class TestRegistry:
    def test_hash_changes_with_params_and_code(self):
        a = candidate_hash(S.TrendSMA, S.TrendSMA.params())
        assert a == candidate_hash(S.TrendSMA, S.TrendSMA.params())
        assert a != candidate_hash(S.TrendSMA, S.TrendSMA.params({"n": 150}))
        assert a != candidate_hash(S.RSI2MeanReversion, S.TrendSMA.params())

    def test_trials_logged_and_counted_unique(self, reg, prices):
        lab.evaluate_research("TrendSMA_v1", {}, reg, prices)
        lab.evaluate_research("TrendSMA_v1", {}, reg, prices)
        lab.evaluate_research("TrendSMA_v1", {"n": 100}, reg, prices)
        n, srs = reg.trial_stats()
        assert n == 2 and len(srs) == 2
        assert len(reg.trials()) == 3

    def test_register_requires_gate_and_rejects_duplicates(self, reg, prices, monkeypatch):
        c, rep = lab.register("RSI2MeanReversion_v1", {}, reg, "test", date(2016, 1, 4), prices=prices)
        assert c is None and not rep["passed"]
        c, rep = lab.register("TrendSMA_v1", {}, reg, "test", date(2016, 1, 4), prices=prices, force=True)
        assert c.status == "incubating" and c.id == "TrendSMA_v1-001"
        with pytest.raises(ValueError):
            lab.register("TrendSMA_v1", {}, reg, "igen", date(2016, 1, 4), prices=prices, force=True)
        assert Registry(reg.path, reg.trials_path).get(c.id).hash == c.hash


class TestGates:
    base = {"days": 252 * 10, "sharpe": 0.8, "max_dd": -0.2, "trades": 50, "total_return": 1.0}

    def test_research_pass(self):
        ok, fails = gates.research_gate(self.base, 0.95, [0.1, 0.2, -0.05, 0.3], {"sharpe": 0.6})
        assert ok, fails

    def test_research_fails(self):
        ok, fails = gates.research_gate({**self.base, "sharpe": 0.5}, 0.5, [0.1, -0.1, -0.2, 0.1], {"sharpe": 0.6})
        assert not ok
        txt = " ".join(fails)
        assert "Deflated" in txt and "SPY" in txt and "delperioder" in txt

    def test_forward(self):
        f = {"days": 70, "total_return": 0.04, "max_dd": -0.08, "sharpe": 0.9}
        assert gates.forward_gate(f)[0]
        assert not gates.forward_gate({**f, "days": 30})[0]
        assert not gates.forward_gate({**f, "total_return": -0.01})[0]


class TestForwardEvaluation:
    def _cand(self, reg, registered, params=None):
        p = S.TrendSMA.params(params)
        c = Candidate("T-1", "TrendSMA_v1", p, candidate_hash(S.TrendSMA, p), "incubating", registered)
        reg.add(c)
        return c

    def test_forward_uses_only_data_after_registration(self, reg, prices):
        c = self._cand(reg, str(prices.close.index[-40].date()))
        lab.evaluate_all(reg, prices=prices)
        assert c.forward["days"] == 39
        assert c.status == "incubating"  # for kort

    def test_ready_after_long_profitable_forward(self, reg):
        p = synthetic(drift=0.002, seed=5)  # stærkt stigende marked
        c = self._cand(reg, str(p.close.index[-200].date()))
        changes = lab.evaluate_all(reg, prices=p)
        assert c.status == "ready", c.forward
        assert any("KLAR" in x for x in changes)

    def test_code_change_retires(self, reg, prices):
        c = self._cand(reg, str(prices.close.index[-100].date()))
        c.hash = "deadbeef"
        lab.evaluate_all(reg, prices=prices)
        assert c.status == "retired"


# -- executor ---------------------------------------------------------------
class FakeStockBroker:
    def __init__(self, accounts=("DU123",), cash=150.0, positions=None, px=None):
        self._accounts = list(accounts)
        self.cash = cash
        self.pos = positions or {}
        self.px = px or {}
        self.orders = []

    def connect(self):
        pass

    def accounts(self):
        return self._accounts

    def cash_usd(self):
        return self.cash

    def positions(self, syms):
        return {s: self.pos.get(s, 0.0) for s in syms}

    def quote(self, s):
        p = self.px.get(s, 100.0)
        return p - 0.01, p + 0.01, p

    def place_limit(self, s, action, qty, price):
        self.orders.append((s, action, qty, price))
        if action == "BUY":
            self.cash -= qty * price
        else:
            self.cash += qty * price
        self.pos[s] = self.pos.get(s, 0.0) + (qty if action == "BUY" else -qty)
        return NS(qty=qty, price=price)

    def status(self, t):
        return NS(filled=t.qty, avg_price=t.price, done=True)

    def sleep(self, s):
        pass

    def cancel(self, t):
        pass


class Rec:
    def __init__(self):
        self.msgs = []

    def send(self, m, level=None):
        self.msgs.append(m)

    def error(self, m):
        self.msgs.append("ERR " + m)


def lab_cfg(tmp_path, **kw):
    from tradelab.config import LabConfig

    c = LabConfig(project_dir=tmp_path)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class TestExecutor:
    def test_plan_orders_sells_first_and_band(self):
        orders = plan_orders({"SPY": 1.0, "BIL": 0.0}, {"SPY": 0.0, "BIL": 1.4}, {"SPY": 100, "BIL": 100},
                             alloc=140, band=0.05, fractional=True)
        assert [(o.symbol, round(o.delta, 4)) for o in orders] == [("BIL", -1.4), ("SPY", 1.4)]
        assert plan_orders({"SPY": 0.98}, {"SPY": 1.4}, {"SPY": 100}, 140, 0.05, True) == []

    def test_plan_orders_whole_shares(self):
        (o,) = plan_orders({"SPY": 1.0}, {}, {"SPY": 600}, 1500, 0.05, fractional=False)
        assert o.delta == 2

    def test_mode_safety(self, tmp_path):
        with pytest.raises(Refused):
            check_mode("paper", lab_cfg(tmp_path), ["U999"])  # rigtig konto i papir-tilstand
        with pytest.raises(Refused):
            check_mode("live", lab_cfg(tmp_path), ["U999"])  # live kræver port 4001
        with pytest.raises(Refused):
            check_mode("live", lab_cfg(tmp_path, ib_port=4001), ["DU1"])
        check_mode("live", lab_cfg(tmp_path, ib_port=4001), ["U999"])
        check_mode("paper", lab_cfg(tmp_path), ["DU1"])

    def _ready(self, reg):
        p = S.TrendSMA.params()
        c = Candidate("T-1", "TrendSMA_v1", p, candidate_hash(S.TrendSMA, p), "ready", "2015-01-01")
        reg.add(c)
        return c

    def test_live_requires_matching_approval(self, tmp_path, reg):
        c = self._ready(reg)
        ap = Approvals(tmp_path / "approvals.json")
        cfg = lab_cfg(tmp_path, ib_port=4001)
        with pytest.raises(Refused, match="godkendelse"):
            select_candidate("live", cfg, reg, ap)
        ap.approve(c.id, "forkerthash", 150, "test")
        with pytest.raises(Refused, match="ændret"):
            select_candidate("live", cfg, reg, ap)
        ap.approve(c.id, c.hash, 150, "test")
        assert select_candidate("live", cfg, reg, ap) is c
        c.status = "incubating"
        with pytest.raises(Refused):
            select_candidate("live", cfg, reg, ap)

    def test_paper_rebalance_buys_target(self, tmp_path, reg, prices):
        self._ready(reg)
        brk = FakeStockBroker(px={"SPY": float(prices.close["SPY"].iloc[-1]), "BIL": float(prices.close["BIL"].iloc[-1])})
        today = (prices.close.index[-1] + pd.Timedelta(days=1)).date()
        out = rebalance("paper", lab_cfg(tmp_path), brk, reg, Approvals(tmp_path / "a.json"), Rec(), today, prices)
        assert out == "traded"
        w = engine.target_weights(S.TrendSMA, S.TrendSMA.params(), prices.close[["SPY", "BIL"]]).iloc[-1]
        held = max(w.items(), key=lambda kv: kv[1])[0]
        assert brk.orders and brk.orders[0][0] == held and brk.orders[0][1] == "BUY"
        assert brk.cash >= 0
        assert (tmp_path / "lab_fills.csv").exists()

    def test_dry_run_sends_nothing(self, tmp_path, reg, prices):
        self._ready(reg)
        brk = FakeStockBroker()
        today = (prices.close.index[-1] + pd.Timedelta(days=1)).date()
        rebalance("paper", lab_cfg(tmp_path, dry_run=True), brk, reg, Approvals(tmp_path / "a.json"), Rec(), today, prices)
        assert brk.orders == []

    def test_live_kill_switch_liquidates_and_revokes(self, tmp_path, reg, prices):
        c = self._ready(reg)
        ap = Approvals(tmp_path / "a.json")
        ap.approve(c.id, c.hash, 1000, "test")
        cfg = lab_cfg(tmp_path, ib_port=4001)
        (tmp_path / "lab_live_state.json").write_text(json.dumps({"candidate": c.id, "peak": 300.0}))
        brk = FakeStockBroker(accounts=["U1"], cash=10.0, positions={"SPY": 2.0}, px={"SPY": 100.0, "BIL": 90.0})
        rec = Rec()
        today = (prices.close.index[-1] + pd.Timedelta(days=1)).date()
        rebalance("live", cfg, brk, reg, ap, rec, today, prices)  # værdi 210 < 300*0.8
        assert ("SPY", "SELL", 2.0) == brk.orders[0][:3]
        assert not any(o[1] == "BUY" for o in brk.orders)
        assert Approvals(tmp_path / "a.json").active is None
        assert any("NØDSTOP" in m for m in rec.msgs)

    def test_switching_candidate_sells_old_positions(self, tmp_path, reg, prices):
        self._ready(reg)
        dm = S.DualMomentum.params()
        reg.add(Candidate("D-1", "DualMomentum_v1", dm, candidate_hash(S.DualMomentum, dm), "retired", "2015-01-01"))
        brk = FakeStockBroker(cash=0.0, positions={"GLD": 1.5}, px={"GLD": 100.0})
        today = (prices.close.index[-1] + pd.Timedelta(days=1)).date()
        rebalance("paper", lab_cfg(tmp_path), brk, reg, Approvals(tmp_path / "a.json"), Rec(), today, prices)
        assert brk.orders[0][:3] == ("GLD", "SELL", 1.5)
        assert any(o[1] == "BUY" for o in brk.orders)

    def test_completed_sessions_only(self, prices):
        last = prices.close.index[-1]
        p = completed_sessions_only(prices, last)
        assert p.close.index[-1] < last

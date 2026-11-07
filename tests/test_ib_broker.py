"""IBBroker testet mod mockede ib_async-objekter (ingen netværk)."""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from xspbot.broker import IBBroker
from xspbot.config import Config


@pytest.fixture
def ib():
    m = MagicMock()
    m.isConnected.return_value = False
    m.qualifyContracts.side_effect = lambda *cs: [setattr(c, "conId", 999) or c for c in cs]
    return m


@pytest.fixture
def brk(ib):
    return IBBroker(Config(), ib=ib)


def av(tag, value, cur):
    return NS(tag=tag, value=str(value), currency=cur, account="U1", modelCode="")


def test_connect_paper_port(brk, ib):
    brk.connect()
    args, kwargs = ib.connect.call_args
    assert args[:2] == ("127.0.0.1", 4002)
    ib.reqMarketDataType.assert_called_once_with(1)


def test_cash_usd_uses_usd_cash_capped_by_available(brk, ib):
    ib.accountValues.return_value = [
        av("CashBalance", 1000, "DKK"),
        av("CashBalance", 152.3, "USD"),
        av("AvailableFunds", 140.0, "USD"),
        av("NetLiquidation", 999, "BASE"),
    ]
    assert brk.cash_usd() == pytest.approx(140.0)


def test_cash_usd_missing_raises(brk, ib):
    ib.accountValues.return_value = [av("CashBalance", 1000, "DKK")]
    with pytest.raises(RuntimeError):
        brk.cash_usd()


def test_option_positions_filters_and_keeps_avgcost(brk, ib):
    xsp = NS(secType="OPT", symbol="XSP", exchange="", localSymbol="XSP   261110C00682000",
             lastTradeDateOrContractMonth="20261110", strike=682.0, right="C")
    spx = NS(secType="OPT", symbol="SPX", exchange="", localSymbol="SPX", lastTradeDateOrContractMonth="20261110",
             strike=6820.0, right="C")
    stk = NS(secType="STK", symbol="XSP", exchange="", localSymbol="XSP", lastTradeDateOrContractMonth="",
             strike=0.0, right="")
    ib.positions.return_value = [
        NS(contract=xsp, position=2.0, avgCost=123.4),
        NS(contract=spx, position=1.0, avgCost=500.0),
        NS(contract=stk, position=5.0, avgCost=1.0),
        NS(contract=xsp, position=0.0, avgCost=0.0),
    ]
    (p,) = brk.option_positions()
    assert p.qty == 2 and p.avg_cost == 123.4
    assert p.contract.exchange == "SMART"  # sat så ordrer kan routes
    assert p.description == "XSP   261110C00682000"


def test_quote_nan_becomes_none(brk, ib):
    ib.reqMktData.return_value = NS(bid=float("nan"), ask=1.25)
    q = brk.quote(NS(conId=5, exchange="SMART"))
    assert q.bid is None and q.ask == 1.25 and not q.has_bid


def test_quote_negative_bid_is_none(brk, ib):
    ib.reqMktData.return_value = NS(bid=-1.0, ask=0.05)
    assert brk.quote(NS(conId=6, exchange="SMART")).bid is None


def test_opening_range_only_uses_0930_to_1000(brk, ib):
    def bar(h, m, hi, lo):
        # IB returnerer UTC ved formatDate=2. 09:30 ET = 14:30 UTC i november
        return NS(date=datetime(2026, 11, 10, h + 5, m, tzinfo=timezone.utc), high=hi, low=lo)

    ib.reqHistoricalData.return_value = [bar(9, 30, 680, 679), bar(9, 45, 681, 678.5), bar(9, 59, 680.2, 679.1),
                                         bar(10, 0, 690, 670)]
    assert brk.opening_range(date(2026, 11, 10), time(9, 30), time(10, 0)) == (681, 678.5)


def test_chain_requires_today_expiry_and_trading_class(brk, ib):
    ib.reqSecDefOptParams.return_value = [
        NS(tradingClass="XSP", expirations=["20261110", "20261111"], exchange="SMART"),
        NS(tradingClass="XSPW", expirations=["20261112"], exchange="SMART"),
    ]
    ib.reqContractDetails.return_value = [NS(contract=NS(strike=s)) for s in (681.0, 680.0, 682.0)]
    exps, strikes = brk.chain(date(2026, 11, 10))
    assert "20261110" in exps and "20261112" not in exps
    assert strikes == [680.0, 681.0, 682.0]
    exps, strikes = brk.chain(date(2026, 11, 13))
    assert strikes == []


def test_place_limit_builds_day_limit_order(brk, ib):
    ib.placeOrder.side_effect = lambda c, o: NS(order=o, contract=c)
    t = brk.place_limit(NS(exchange="SMART", localSymbol="X"), "BUY", 3, 1.25)
    assert t.order.action == "BUY" and t.order.totalQuantity == 3
    assert t.order.lmtPrice == 1.25 and t.order.tif == "DAY" and t.order.orderType == "LMT"


def test_order_status_and_cancel(brk, ib):
    trade = MagicMock()
    trade.filled.return_value = 2.0
    trade.orderStatus.avgFillPrice = 1.3
    trade.isDone.side_effect = [False, False, True]
    st = brk.order_status(trade)
    assert st.filled == 2 and st.avg_price == 1.3 and st.done is False
    brk.cancel(trade)
    ib.cancelOrder.assert_called_once_with(trade.order)


def test_cancel_open_orders_only_xsp(brk, ib):
    a = NS(contract=NS(symbol="XSP"), order="A", isDone=lambda: False)
    b = NS(contract=NS(symbol="AAPL"), order="B", isDone=lambda: False)
    ib.reqAllOpenOrders.return_value = [a, b]
    assert brk.cancel_open_orders() == 1
    ib.cancelOrder.assert_called_once_with("A")

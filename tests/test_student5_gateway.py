"""Exercise the limit through the actual paper trader, with costs and repeats."""

import random
from decimal import Decimal

import pytest

from risk.gateway import CAP, approve_trade, execute_guarded
from student4.paper_trading import PaperTrader


def assert_capped(trader, price):
    value = trader.btc * Decimal(str(price))
    assert value <= CAP * (trader.cash + value)
    assert trader.cash >= 0


def test_first_buy_counts_fees_and_slippage():
    trader = PaperTrader()
    decision, trade = execute_guarded(trader, "BUY", "50000")
    assert decision.action == "BUY" and trade
    assert Decimal("0.249") < decision.projected_exposure <= CAP
    assert_capped(trader, "50000")


def test_repeated_buys_never_build_past_limit():
    trader = PaperTrader()
    for _ in range(20):
        execute_guarded(trader, "BUY", "50000")
        assert_capped(trader, "50000")


def test_existing_holdings_and_small_requested_buy():
    decision = approve_trade("BUY", cash_usd="8000", btc="0.04", btc_price_usd="50000")
    assert decision.buy_fraction < Decimal("0.063")  # about $500 room, not $2000
    trader = PaperTrader()
    decision, _ = execute_guarded(trader, "BUY", "50000", requested_buy_fraction="0.01")
    assert decision.buy_fraction <= Decimal("0.01")
    assert_capped(trader, "50000")


def test_price_drift_forces_full_sell_on_hold():
    trader = PaperTrader()
    execute_guarded(trader, "BUY", "50000")
    decision, trade = execute_guarded(trader, "HOLD", "80000")
    assert decision.action == "SELL" and trade
    assert trader.btc == 0


def test_exact_cap_blocks_buy_and_empty_account_holds():
    decision = approve_trade("BUY", cash_usd=7500, btc=0.05, btc_price_usd=50000)
    assert decision.action == "HOLD"
    decision = approve_trade("BUY", cash_usd=0, btc=0, btc_price_usd=50000)
    assert decision.action == "HOLD"


@pytest.mark.parametrize("key,value", [
    ("cash_usd", -1), ("btc", float("nan")), ("btc_price_usd", 0),
    ("cash_usd", True), ("requested_buy_fraction", 2), ("fee_rate", 1),
    ("slippage_bps", 10000), ("btc_price_usd", float("inf")),
])
def test_bad_account_or_costs_rejected(key, value):
    args = dict(cash_usd=10000, btc=0, btc_price_usd=50000)
    args[key] = value
    with pytest.raises(ValueError):
        approve_trade("BUY", **args)


def test_randomized_costs_balances_and_price_paths():
    rng = random.Random(5)
    for _ in range(40):
        trader = PaperTrader(initial_cash=str(rng.uniform(100, 100000)),
                             fee_rate=str(rng.uniform(0, 0.01)),
                             slippage_bps=str(rng.uniform(0, 100)))
        for _ in range(10):
            price = str(rng.uniform(10000, 200000))
            execute_guarded(trader, rng.choice(["BUY", "HOLD", "SELL"]), price,
                            requested_buy_fraction=str(rng.random()))
            assert_capped(trader, price)

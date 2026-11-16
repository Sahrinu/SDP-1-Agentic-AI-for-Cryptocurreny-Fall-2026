"""25% preview and strict paper gateway tests; all policy values synthetic."""
import random
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import pytest
from risk.gateway import CAP, approve_trade, execute_guarded
from risk.durable_gateway import RiskGateway, RiskPolicy
from execution.paper_trading import PaperTrader

NOW=datetime(2026,1,2,tzinfo=timezone.utc)

@pytest.fixture
def gateway_factory(tmp_path):
    gateways=[]
    def make(trader):
        policy=RiskPolicy('1','1')
        path=tmp_path/f'risk-{len(gateways)}.sqlite3'
        RiskGateway.initialize_state(path,policy=policy,trader=trader,actor='synthetic-test',
             reason='isolated risk test',timestamp=NOW-timedelta(seconds=1),previous_forecast='.5')
        gateway=RiskGateway(trader,policy,state_path=path)
        gateways.append(gateway)
        return gateway
    yield make
    for gateway in gateways:gateway.close()


def assert_capped(trader, price):
    value=trader.btc*Decimal(str(price))
    assert value<=CAP*(trader.cash+value)
    assert trader.cash>=0


def test_first_buy_counts_fees_and_slippage(gateway_factory):
    trader=PaperTrader();gateway_factory(trader)
    preview=approve_trade('BUY',cash_usd=trader.cash,btc=trader.btc,btc_price_usd='50000',
                          fee_rate=trader.fee_rate,slippage_bps=trader.slippage*10000)
    assert Decimal('.249') < preview.projected_exposure <= CAP
    decision,trade=execute_guarded(trader,'BUY','50000',requested_buy_fraction=str(preview.buy_fraction),
                                  timestamp=NOW,probability_up='.5')
    assert decision.action=='BUY' and trade
    assert_capped(trader,'50000')


def test_repeated_buys_never_build_past_limit(gateway_factory):
    trader=PaperTrader();gateway_factory(trader)
    for index in range(20):
        execute_guarded(trader,'BUY','50000',requested_buy_fraction='.02',
                        timestamp=NOW+timedelta(seconds=index),probability_up='.5')
        assert_capped(trader,'50000')


def test_existing_holdings_and_small_requested_buy(gateway_factory):
    decision=approve_trade('BUY',cash_usd='8000',btc='.04',btc_price_usd='50000')
    assert decision.buy_fraction<Decimal('.063')
    trader=PaperTrader();gateway_factory(trader)
    decision,trade=execute_guarded(trader,'BUY','50000',requested_buy_fraction='.01',
                                  timestamp=NOW,probability_up='.5')
    assert trade and decision.buy_fraction==Decimal('.01')
    assert_capped(trader,'50000')


def test_price_drift_does_not_force_sell_on_hold(gateway_factory):
    trader=PaperTrader();gateway_factory(trader)
    _,trade=execute_guarded(trader,'BUY','50000',requested_buy_fraction='.24',timestamp=NOW,probability_up='.5')
    assert trade
    before=(trader.cash,trader.btc,tuple(trader.trades))
    preview=approve_trade('HOLD',cash_usd=trader.cash,btc=trader.btc,btc_price_usd='80000',
                          fee_rate=trader.fee_rate,slippage_bps=trader.slippage*10000)
    assert preview.action=='SELL'  # Legacy preview is not an order.
    decision,trade=execute_guarded(trader,'HOLD','80000',timestamp=NOW+timedelta(seconds=1),probability_up='.5')
    assert decision.action=='HOLD' and trade is None
    assert (trader.cash,trader.btc,tuple(trader.trades))==before
    assert 'exposure' in decision.reason.lower()


def test_price_drift_forces_full_sell_on_hold():
    # Preserve the teammate sizing-preview policy; it never submits an order.
    decision = approve_trade("HOLD", cash_usd="7500", btc="0.05", btc_price_usd="80000")
    assert decision.action == "SELL" and decision.projected_exposure == 0


def test_exact_cap_blocks_buy_and_empty_account_holds():
    assert approve_trade('BUY',cash_usd=7500,btc=.05,btc_price_usd=50000).action=='HOLD'
    assert approve_trade('BUY',cash_usd=0,btc=0,btc_price_usd=50000).action=='HOLD'


@pytest.mark.parametrize('key,value', [('cash_usd',-1),('btc',float('nan')),('btc_price_usd',0),
    ('cash_usd',True),('requested_buy_fraction',2),('fee_rate',1),('slippage_bps',10000),
    ('btc_price_usd',float('inf'))])
def test_bad_account_or_costs_rejected(key,value):
    args=dict(cash_usd=10000,btc=0,btc_price_usd=50000);args[key]=value
    with pytest.raises(ValueError):approve_trade('BUY',**args)


def test_randomized_costs_balances_and_price_paths(gateway_factory):
    rng=random.Random(5)
    for _ in range(40):
        trader=PaperTrader(initial_cash=str(rng.uniform(100,100000)),
                           fee_rate=str(rng.uniform(0,.01)),slippage_bps=str(rng.uniform(0,100)))
        gateway_factory(trader)
        for index in range(10):
            price=str(rng.uniform(10000,200000));action=rng.choice(['BUY','HOLD','SELL'])
            before=(trader.cash,trader.btc,tuple(trader.trades))
            decision,trade=execute_guarded(trader,action,price,requested_buy_fraction=str(rng.random()),
                                           timestamp=NOW+timedelta(seconds=index),probability_up='.5')
            if trade is None:assert (trader.cash,trader.btc,tuple(trader.trades))==before
            else:
                assert decision.action==action
                assert_capped(trader,price)

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy
from risk.gateway import execute_guarded

NOW=datetime(2026,11,13,tzinfo=timezone.utc)


def configured(tmp_path):
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    policy=RiskPolicy('.2','.1')  # Synthetic thresholds.
    path=tmp_path/'state.sqlite3'
    RiskGateway.initialize_state(path,policy=policy,trader=trader,actor='synthetic-test',
        reason='test',timestamp=NOW-timedelta(minutes=1),previous_forecast='.5')
    return trader,RiskGateway(trader,policy,state_path=path)


def snapshot(trader):
    return trader.cash,trader.btc,tuple(trader.trades)


def test_guarded_blocks_excess_buy_and_does_not_resize(tmp_path):
    trader,gateway=configured(tmp_path)
    try:
        before=snapshot(trader)
        decision,trade=execute_guarded(trader,'BUY','100',requested_buy_fraction='.9',
                                       timestamp=NOW,probability_up='.55')
        assert trade is None and snapshot(trader)==before
        assert decision.action=='HOLD' and 'exposure' in decision.reason.lower()
    finally:gateway.close()


def test_guarded_halt_and_missing_forecast_never_liquidate(tmp_path):
    trader,gateway=configured(tmp_path)
    try:
        decision,trade=execute_guarded(trader,'BUY','100',requested_buy_fraction='.25',
                                       timestamp=NOW,probability_up='.5')
        assert trade is not None and len(trader.trades)==1
        before=snapshot(trader)
        decision,trade=execute_guarded(trader,'HOLD','200',timestamp=NOW+timedelta(minutes=1),probability_up=None)
        assert trade is None and snapshot(trader)==before and decision.action=='HOLD'
        assert 'forecast' in decision.reason.lower()
        decision,trade=execute_guarded(trader,'BUY','200',requested_buy_fraction='.1',
                                       timestamp=NOW+timedelta(minutes=2),probability_up='.7')
        assert trade is None and snapshot(trader)==before and gateway.halted
        decision,trade=execute_guarded(trader,'SELL','200',timestamp=NOW+timedelta(minutes=3),probability_up='.5')
        assert trade is None and snapshot(trader)==before and 'circuit breaker' in decision.reason.lower()
    finally:gateway.close()


def test_guarded_unconfigured_fails_closed():
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    before=snapshot(trader)
    decision,trade=execute_guarded(trader,'BUY','100',requested_buy_fraction='.1',timestamp=NOW,probability_up='.5')
    assert trade is None and snapshot(trader)==before and decision.action=='HOLD'
    assert 'risk gateway' in decision.reason.lower()


def test_guarded_both_entry_points_trip_swing_with_invalid_order_inputs(tmp_path):
    for entry in ('public', 'guarded'):
        directory = tmp_path / entry
        directory.mkdir()
        trader, gateway = configured(directory)
        try:
            before = snapshot(trader)
            with pytest.raises(ValueError):
                if entry == 'public':
                    trader.execute('BUY', None, buy_fraction='invalid',
                                   timestamp=NOW, probability_up='.7')
                else:
                    execute_guarded(trader, 'BUY', None, requested_buy_fraction='invalid',
                                    timestamp=NOW, probability_up='.7')
            assert snapshot(trader) == before
            assert trader.last_risk_outcome['status'] == 'halted'
            assert 'forecast swing' in trader.last_risk_outcome['reason']
        finally:
            gateway.close()


def test_public_entry_blocks_post_fee_cap_breach_without_mutating_account(tmp_path):
    trader = PaperTrader('10000', fee_rate='.01', slippage_bps='100')
    policy = RiskPolicy('.9', '.9')
    path = tmp_path / 'fee-state.sqlite3'
    RiskGateway.initialize_state(path, policy=policy, trader=trader, actor='synthetic-test',
        reason='test', timestamp=NOW-timedelta(minutes=1), previous_forecast='.5')
    gateway = RiskGateway(trader, policy, state_path=path)
    try:
        before = (trader.cash, trader.btc, trader.cost_basis, trader.realized_pnl,
                  trader.total_fees, tuple(trader.trades))
        trade = trader.execute('BUY', '100', buy_fraction='.254', timestamp=NOW, probability_up='.5')
        assert trade is None and trader.last_risk_outcome['status'] == 'blocked'
        assert Decimal(trader.last_risk_outcome['projected_btc_exposure']) > Decimal('.25')
        assert (trader.cash, trader.btc, trader.cost_basis, trader.realized_pnl,
                trader.total_fees, tuple(trader.trades)) == before
    finally:
        gateway.close()


def test_invalid_sell_size_never_fills_during_breaker_evaluation(tmp_path):
    trader, gateway = configured(tmp_path)
    try:
        assert trader.execute('BUY', '100', buy_fraction='.2', timestamp=NOW,
                              probability_up='.5') is not None
        before = snapshot(trader)
        with pytest.raises(ValueError):
            trader.execute('SELL', '100', buy_fraction='invalid',
                           timestamp=NOW+timedelta(minutes=1), probability_up='.5')
        assert snapshot(trader) == before
        assert trader.last_risk_outcome['status'] == 'blocked'
    finally:
        gateway.close()

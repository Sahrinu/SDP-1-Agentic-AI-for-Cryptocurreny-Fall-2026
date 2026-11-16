"""Offline, synthetic boundary tests for the task 5 paper-risk gateway."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def durable_test_state(tmp_path):
    global _state_dir, _gateways
    _state_dir, _gateways = tmp_path, []
    yield
    for risk in _gateways:
        risk.close()


def gateway(*, previous='0.5', swing='0.2', loss='0.1', fee='0', slip='0'):
    trader = PaperTrader('10000', fee_rate=fee, slippage_bps=slip)
    policy = RiskPolicy(swing, loss)
    path = _state_dir / f'risk-{len(_gateways)}.sqlite3'
    RiskGateway.initialize_state(path, policy=policy, trader=trader, actor='synthetic-test',
                                 reason='Initialize isolated test', timestamp=NOW-timedelta(days=1),
                                 previous_forecast=previous)
    risk = RiskGateway(trader, policy, state_path=path)
    _gateways.append(risk)
    return trader, risk


def execute(risk, action='BUY', *, p='0.5', price='100', fraction='0.1', when=NOW):
    return risk.execute(action, probability_up=p, btc_price_usd=price,
                        timestamp=when, buy_fraction=fraction, explanation='Synthetic test')


def test_exact_exposure_cap_executes_and_just_above_is_blocked():
    trader, risk = gateway()
    result = execute(risk, fraction='0.25')
    assert result['status'] == 'executed'
    assert Decimal(result['btc_exposure_after']) == Decimal('0.25')
    assert trader.cash == Decimal('7500') and trader.btc == Decimal('25')

    trader, risk = gateway()
    result = execute(risk, fraction='0.25000001')
    assert result['status'] == 'blocked'
    assert 'exposure' in result['reason'].lower()
    assert Decimal(result['projected_btc_exposure']) > Decimal('0.25')
    assert Decimal(result['btc_exposure_after']) == 0
    assert trader.cash == Decimal('10000') and trader.btc == 0 and not trader.trades


def test_cap_uses_projected_fee_slippage_and_marked_equity():
    trader, risk = gateway(fee='0.01', slip='100')
    result = execute(risk, fraction='0.2525')
    assert result['status'] == 'executed'
    equity = trader.cash + trader.btc * Decimal('100')
    assert Decimal(result['btc_exposure_after']) == trader.btc * Decimal('100') / equity
    assert Decimal(result['btc_exposure_after']) <= Decimal('0.25')
    assert Decimal(result['trade']['fee_usd']) > 0
    assert Decimal(result['trade']['execution_price_usd']) == Decimal('101')

    trader, risk = gateway(fee='0.01', slip='100')
    result = execute(risk, fraction='0.254')
    assert result['status'] == 'blocked'
    assert Decimal(result['projected_btc_exposure']) > Decimal('0.25')
    assert Decimal(result['btc_exposure_after']) == 0
    assert not trader.trades and trader.btc == 0


def test_existing_breach_blocks_hold_and_buy_but_allows_reduction():
    trader, risk = gateway(loss='1')
    assert execute(risk, fraction='0.25')['status'] == 'executed'
    for action in ('HOLD', 'BUY'):
        result = execute(risk, action, price='200', when=NOW + timedelta(minutes=1))
        assert result['status'] == 'blocked'
        assert 'existing btc exposure' in result['reason'].lower()
        assert len(trader.trades) == 1 and trader.btc == 25
    result = execute(risk, 'SELL', price='200', when=NOW + timedelta(minutes=2))
    assert result['status'] == 'executed'
    assert trader.btc == 0 and Decimal(result['btc_exposure_after']) == 0


def test_swing_at_limit_latches_and_blocks_even_a_reducing_sell():
    trader, risk = gateway(swing='0.2')
    assert execute(risk, 'HOLD', p='0.69999999')['status'] == 'no_trade'

    trader, risk = gateway(swing='0.2')
    result = execute(risk, p='0.7')
    assert result['status'] == 'halted' and result['breaker_halted'] is True
    assert Decimal(result['forecast_swing']) == Decimal('0.2')
    later = execute(risk, 'SELL', p='0.6', when=NOW + timedelta(minutes=1))
    assert later['status'] == 'halted' and later['trade'] is None
    assert later['timestamp'] == (NOW + timedelta(minutes=1)).isoformat()
    assert later['probability_up'] == 0.6
    assert not trader.trades and trader.btc == 0


def test_loss_breaker_runs_with_missing_forecast():
    trader, risk = gateway(loss='0.1', swing='1')
    assert execute(risk, fraction='0.2')['status'] == 'executed'
    result = execute(risk, 'SELL', p=None, price='50', when=NOW + timedelta(minutes=1))
    assert result['status'] == 'halted'
    assert Decimal(result['cumulative_loss_fraction']) == Decimal('0.1')
    assert 'loss' in result['reason'].lower()
    assert trader.btc == Decimal('20') and len(trader.trades) == 1


def test_swing_breaker_runs_with_missing_price():
    trader, risk = gateway(swing='0.2')
    result = execute(risk, p='0.7', price=None)
    assert result['status'] == 'halted'
    assert 'forecast swing' in result['reason'].lower()
    assert not trader.trades


@pytest.mark.parametrize('change', [
    {'price': None}, {'price': 0}, {'price': 'NaN'}, {'p': None},
    {'p': True}, {'p': 1.01}, {'fraction': None}, {'fraction': 0},
    {'fraction': 1.1}, {'when': None}, {'when': datetime(2026, 11, 13, 12)},
])
def test_missing_or_invalid_inputs_do_not_execute(change):
    trader, risk = gateway()
    result = execute(risk, **change)
    assert result['status'] == 'blocked'
    assert result['trade'] is None and not trader.trades
    assert trader.cash == Decimal('10000') and trader.btc == 0


def test_missing_previous_forecast_blocks_but_real_half_is_valid():
    trader, risk = gateway(previous=None)
    result = execute(risk, p='0.5')
    assert result['status'] == 'blocked' and 'previous forecast' in result['reason'].lower()
    assert result['probability_up'] == 0.5 and not trader.trades
    trader, risk = gateway(previous='0.5')
    result = execute(risk, 'HOLD', p='0.5')
    assert result['status'] == 'no_trade' and result['probability_up'] == 0.5


@pytest.mark.parametrize('swing,loss', [(None, .1), (0, .1), (True, .1), (.2, None), (.2, 0), (.2, 1.1)])
def test_policy_requires_explicit_valid_thresholds(swing, loss):
    with pytest.raises(ValueError):
        RiskPolicy(swing, loss)


def test_nonchronological_request_is_blocked():
    trader, risk = gateway()
    assert execute(risk, 'HOLD')['status'] == 'no_trade'
    result = execute(risk, when=NOW - timedelta(seconds=1))
    assert result['status'] == 'blocked' and 'chronological' in result['reason']
    assert not trader.trades


def test_only_the_known_paper_trader_can_be_wrapped():
    class OtherTrader(PaperTrader):
        def execute(self,*args,**kwargs):
            raise AssertionError('Unverified execution must not be used')
    with pytest.raises(ValueError):
        RiskGateway(OtherTrader('10000'),RiskPolicy('.2','.1'),state_path=_state_dir/'unused.sqlite3',previous_forecast='.5')

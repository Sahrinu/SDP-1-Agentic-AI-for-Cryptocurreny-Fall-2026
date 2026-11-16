"""Synthetic offline checks for main dashboard and durable paper risk."""
from datetime import datetime, timedelta, timezone

from dashboard.service import PaperStore
from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy

NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)


def forecast(identifier, probability, price='100'):
    return {'id':identifier, 'created_at':NOW.isoformat(), 'mode':'live',
            'methods':{'exchange':probability}, 'quote':{'price':price}, 'cap_feasible':True}


def configured(tmp_path, *, swing='.9'):
    state=tmp_path/'risk.sqlite3'
    policy=RiskPolicy(swing,'.9')  # synthetic thresholds only
    RiskGateway.initialize_state(state,policy=policy,trader=PaperTrader(),actor='synthetic-test',
        reason='initialize synthetic account',timestamp=NOW-timedelta(minutes=1),previous_forecast='.5')
    return PaperStore(tmp_path/'paper.sqlite3',risk_policy=policy,risk_state_path=state,buy_fraction='.24'),state,policy


def test_unconfigured_dashboard_blocks_and_preserves_account(tmp_path):
    result=PaperStore(tmp_path/'paper.sqlite3').decide(forecast('one',.6),now=NOW)
    assert result['trade'] is None
    assert result['portfolio']['cash_usd']=='10000' and result['portfolio']['btc']=='0'
    assert 'required' in result['risk']['reason']
    assert result['risk']['status']=='blocked' and not result['breaker']['configured']


def test_dashboard_reports_influence_cap_block_and_configured_policy(tmp_path):
    store,_,_=configured(tmp_path)
    item=forecast('influence-block',.6)
    item['cap_feasible']=False
    result=store.decide(item,now=NOW)
    assert result['requested']=='BUY' and result['constrained']=='HOLD'
    assert result['risk']['status']=='blocked' and 'influence cap' in result['risk']['reason']
    assert result['trade'] is None and result['portfolio']['cash_usd']=='10000'
    assert result['breaker']['configured'] and result['breaker']['max_cumulative_loss']=='0.9'


def test_missing_forecast_cannot_liquidate_overweight_account(tmp_path):
    store,_,_=configured(tmp_path)
    first=store.decide(forecast('buy',.6),now=NOW)
    assert first['trade'] is not None
    second=store.decide(forecast('abstain',None,'200'),now=NOW+timedelta(seconds=1))
    assert second['trade'] is None and second['risk']['action']=='HOLD'
    assert second['portfolio']['cash_usd']==first['portfolio']['cash_usd']
    assert second['portfolio']['btc']==first['portfolio']['btc']
    assert second['portfolio']['trade_count']==first['portfolio']['trade_count']
    assert 'forecast' in second['risk']['reason'].lower()


def test_halt_survives_reopening_dashboard(tmp_path):
    store,state,policy=configured(tmp_path,swing='.1')
    first=store.decide(forecast('halt',.7),now=NOW)
    assert first['breaker']['halted'] and first['trade'] is None
    restarted=PaperStore(tmp_path/'paper.sqlite3',risk_policy=policy,risk_state_path=state,buy_fraction='.24')
    second=restarted.decide(forecast('later',.6),now=NOW+timedelta(seconds=1))
    assert second['breaker']['halted'] and second['trade'] is None
    assert second['portfolio']['cash_usd']=='10000' and second['portfolio']['btc']=='0'


def test_gateway_and_dashboard_account_mismatch_blocks_instead_of_replaying(tmp_path):
    store,state,policy=configured(tmp_path)
    # Simulate a process stopping after the risk state commits but before dashboard state commits.
    trader=PaperTrader()
    gate=RiskGateway(trader,policy,state_path=state)
    try:
        assert gate.execute('BUY',probability_up='.6',btc_price_usd='100',
                            timestamp=NOW,buy_fraction='.24')['status']=='executed'
    finally:
        gate.close()
    result=store.decide(forecast('after-crash',.6),now=NOW+timedelta(seconds=1))
    assert result['trade'] is None and 'does not match' in result['risk']['reason']
    assert result['portfolio']['cash_usd']=='10000' and result['portfolio']['btc']=='0'


def test_existing_synthetic_student5_demo_uses_real_risk_outcomes(tmp_path):
    from examples.student5_demo import main
    result=main(tmp_path/'demo')
    assert result['synthetic']
    assert [row['outcome']['status'] for row in result['risk_checks']] == ['executed','blocked','halted']
    assert [row['event'] for row in result['risk_audit']] == ['initialize','halt']
    assert (tmp_path/'demo'/'student5_demo.json').is_file()

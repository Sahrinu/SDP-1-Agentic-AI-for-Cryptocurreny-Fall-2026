"""Live collector -> explicit forecast adapter, using fabricated book frames."""
from copy import deepcopy
from datetime import timedelta
import json
import pytest
from market_data import live
from dashboard.service import forecast_snapshot
from tests.test_market_evidence_specialist import observation,response,PRED


def test_collected_book_reaches_dashboard_betting_forecast(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'now',lambda:PRED)
    data,status=live.collect_betting_inputs(tmp_path,observe=observation)
    assert status['status']=='collected'
    assert json.loads((tmp_path/'betting_observation.json').read_text())['quote']==observation()['quote']
    quote={'price':70000,'bar_close_time':(PRED-timedelta(seconds=1)).isoformat(),
           'retrieved_at':PRED.isoformat()}
    calls=[]
    def model(prompt):calls.append(prompt);return json.dumps(response())
    result=forecast_snapshot(data,quote,client=model,decision_time=PRED)
    betting=next(r for r in result['agents'] if r['role']=='Betting odds')
    assert betting['p_up']==.52 and betting['audit']['passed']
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['failed','stale','tampered','synthetic'])
def test_unusable_capture_produces_no_adapter_input(tmp_path,monkeypatch,mutation):
    monkeypatch.setattr(live,'now',lambda:PRED)
    obs=deepcopy(observation())
    if mutation=='failed':obs={'observation_status':'failed','stage':'book_stream'}
    if mutation=='stale':obs['quote']['received_at']=(PRED-timedelta(minutes=6)).isoformat()
    if mutation=='tampered':obs['quote']['best_bid']=.99
    if mutation=='synthetic':obs['synthetic']=True
    data,status=live.collect_betting_inputs(tmp_path,observe=lambda:obs)
    assert data=={} and status['status']=='unavailable'
    assert (tmp_path/'betting_observation.json').exists()


def test_failed_refresh_discards_previous_book_and_price_context(tmp_path):
    data={'student2_market_observation':observation(),'student2_markets_observed_at':PRED.isoformat(),
          'student2_price_context':{'old':True},'other_input':'preserved'}
    status=live.refresh_betting_inputs(data,tmp_path,observe=lambda:{'observation_status':'failed'})
    assert status['status']=='unavailable' and data=={'other_input':'preserved'}


def test_legacy_limitless_observer_passes_timeout_and_reports_wait(tmp_path, monkeypatch):
    from market_data.prediction_markets import limitless_live, limitless_targeted_discovery

    market = {'slug': 'test-market'}
    monkeypatch.setattr(limitless_targeted_discovery, 'discover', lambda **_: (market, {}))

    def capture(selected, *, timeout_seconds):
        assert selected is market
        assert timeout_seconds == 30
        raise limitless_live.InitialBookOnlyError()

    def observe_market(*, targeted_market, capture):
        with pytest.raises(limitless_live.InitialBookOnlyError):
            capture(targeted_market)
        return {'observation_status': 'failed', 'stage': 'book_stream',
                'error_type': 'InitialBookOnlyError', 'book_wait_seconds': 30.0}

    monkeypatch.setattr(limitless_live, 'capture_book', capture)
    monkeypatch.setattr(limitless_live, 'observe_market', observe_market)
    data, status = live.collect_betting_inputs(tmp_path,
        observe=lambda: limitless_live.observe_current_market(timeout_seconds=30))
    assert data == {}
    assert status['status'] == 'unavailable'
    assert status['book_wait_seconds'] == 30
    assert 'waited 30 seconds' in status['reason']


def test_kalshi_trade_reaches_dashboard_forecast_and_stale_trade_abstains(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from market_data.prediction_markets import kalshi_live

    instant = datetime(2026, 11, 21, 8, tzinfo=timezone.utc)
    market = {'ticker': 'KXBTCD-26NOV2117-T57749.99', 'event_ticker': 'KXBTCD-26NOV2117',
              'status': 'active', 'strike_type': 'greater', 'floor_strike': 57749.99,
              'close_time': '2026-11-21T22:00:00Z', 'yes_bid_dollars': '0.51', 'yes_ask_dollars': '0.52',
              'rules_primary': "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) before 5 PM EST is above 57749.99 at 5 PM EST on Nov 21, 2026, then the market resolves to Yes."}
    trade = {'ticker': market['ticker'], 'trade_id': 'fixture-trade', 'is_block_trade': False,
             'created_time': instant.isoformat(), 'yes_price_dollars': '0.51',
             'no_price_dollars': '0.49', 'count_fp': '5'}

    def get(url, **kwargs):
        return {'trades': [trade]} if '/markets/trades?' in url else {'markets': [market]}

    reader = kalshi_live.observe_bitcoin_trade

    def observe():
        return reader(get=get, clock=lambda: instant)

    monkeypatch.setattr(kalshi_live, 'observe_bitcoin_trade', observe)
    monkeypatch.setattr(live, 'now', lambda: instant)
    data, status = live.collect_betting_inputs(tmp_path)
    assert status['provider'] == 'kalshi' and status['status'] == 'collected'
    result = forecast_snapshot(data, {'price': 57750, 'bar_close_time': instant.isoformat(),
        'retrieved_at': instant.isoformat()}, decision_time=instant,
        client=lambda _: {**response(), 'evidence_ids': ['kalshi-trade-001']})
    betting = next(agent for agent in result['agents'] if agent['role'] == 'Betting odds')
    assert betting['p_up'] == .52 and 'kalshi-trade-001' in betting['evidence']

    import pandas as pd
    data['btc_hourly'] = pd.DataFrame({'close': [57750 + 50 * (-1)**index for index in range(25)]},
        index=pd.date_range(end=instant, periods=25, freq='h'))
    result = forecast_snapshot(data, {'price': 57750, 'bar_close_time': instant.isoformat(),
        'retrieved_at': instant.isoformat()}, decision_time=instant)
    betting = next(agent for agent in result['agents'] if agent['role'] == 'Betting odds')
    assert betting['audit']['passed'] and 0 < betting['p_up'] < 1
    assert 'binance-hourly-returns' in betting['evidence']

    trade['created_time'] = (instant - timedelta(seconds=31)).isoformat()
    monkeypatch.setattr(live.time, 'sleep', lambda _: None)
    data, status = live.collect_betting_inputs(tmp_path)
    assert data == {} and status['status'] == 'unavailable'

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

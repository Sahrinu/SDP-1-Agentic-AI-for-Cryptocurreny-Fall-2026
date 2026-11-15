"""Real progress events, observable in-flight work, exact timings and safe failures."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import json
import threading
import pytest
from agents.registry import CATALOG
from dashboard.activity import ForecastActivity
from dashboard.service import forecast_snapshot

NOW=datetime(2026,11,15,10,tzinfo=timezone.utc)


def quote():
    return {'price':70000,'bar_close_time':(NOW-timedelta(seconds=1)).isoformat(),
            'retrieved_at':NOW.isoformat(),'source':'synthetic unit test only'}


def test_full_forecast_events_match_results_and_execution_order():
    activity=ForecastActivity();activity.begin()
    result=forecast_snapshot({},quote(),decision_time=NOW,on_progress=activity.record)
    activity.finish();progress=activity.snapshot()
    assert progress['status']=='completed' and progress['evaluated_count']==13
    assert progress['abstained_count']==13 and progress['forecasted_count']==0
    starts=[e['role'] for e in progress['events'] if e['kind']=='agent_started']
    ends=[e['role'] for e in progress['events'] if e['kind']=='agent_completed']
    assert starts==ends==[s.name for s in CATALOG]
    assert starts[-1]=='Contrarian'
    assert progress['methods']==result['methods']
    assert all(a['result']['p_up'] is None for a in progress['agents'])
    assert progress['events'][-1]['kind']=='run_completed'
    assert [e['sequence'] for e in progress['events']]==list(range(1,len(progress['events'])+1))


def test_timing_measures_work_and_snapshot_cannot_mutate_live_state():
    clock=SimpleNamespace(now=100.)
    activity=ForecastActivity(clock=lambda:clock.now,wall_clock=lambda:NOW.isoformat());activity.begin()
    activity.record({'event':'agent_started','role':'Chart'})
    clock.now+=.25
    assert activity.snapshot()['agents'][0]['running_ms']==250
    result={'agent':'chart','day':NOW.date().isoformat(),'role':'Chart','p_up':.6,
            'evidence':['test evidence'],'audit':{'passed':True}}
    activity.record({'event':'agent_completed','role':'Chart','status':'forecasted','result':result})
    snapshot=activity.snapshot()
    assert snapshot['agents'][0]['duration_ms']==250
    snapshot['agents'][0]['result']['evidence'].append('must not appear in live state')
    assert activity.snapshot()['agents'][0]['result']['evidence']==['test evidence']
    with pytest.raises(ValueError,match='all specialists'):activity.finish()
    activity.reset();assert activity.snapshot()['status']=='idle'
    assert not activity.snapshot()['events']


def test_inflight_agent_is_visible_before_it_finishes(monkeypatch):
    import dashboard.service as service
    entered,release=threading.Event(),threading.Event()
    def forecast(day,data):
        entered.set()
        assert release.wait(timeout=5)
        return {'agent':'chart','day':day,'p_up':.6,'evidence':['test'],'data_missing':False}
    monkeypatch.setattr(service,'CATALOG',(SimpleNamespace(name='Chart',module='unit.fake',registration='discovery'),))
    monkeypatch.setattr(service.importlib,'import_module',lambda module:SimpleNamespace(forecast=forecast))
    activity=ForecastActivity();activity.begin();results=[];errors=[]
    def run():
        try:results.append(forecast_snapshot({},quote(),decision_time=NOW,on_progress=activity.record))
        except Exception as exc:errors.append(type(exc).__name__)
    worker=threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        snapshot=activity.snapshot()
        assert snapshot['status']=='running' and snapshot['current_agent']=='Chart'
        assert snapshot['agents'][0]['status']=='running' and snapshot['agents'][0]['p_up'] is None
        assert snapshot['evaluated_count']==0
    finally:
        release.set();worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert results[0]['active_count']==1
    assert activity.snapshot()['agents'][0]['status']=='forecasted'


def test_failed_run_marks_unreached_agents_honestly():
    activity=ForecastActivity();activity.begin()
    activity.record({'event':'agent_started','role':'Chart'})
    activity.fail('RuntimeError')
    result=activity.snapshot()
    assert result['status']=='failed'
    assert result['agents'][0]['status']=='failed'
    assert all(a['status']=='not_run' for a in result['agents'][1:])
    assert not result['forecasted_count']


def test_exceptions_never_leak_raw_error_text_to_activity(monkeypatch):
    import dashboard.service as service
    def broken(day,data):raise RuntimeError('unit-test-private-provider-text')
    monkeypatch.setattr(service.importlib,'import_module',lambda module:SimpleNamespace(forecast=broken))
    activity=ForecastActivity();activity.begin()
    forecast_snapshot({},quote(),decision_time=NOW,on_progress=activity.record)
    activity.finish()
    progress=activity.snapshot()
    assert progress['failed_count']==10  # Three adapters abstain on missing inputs.
    assert 'unit-test-private-provider-text' not in json.dumps(progress)
    assert progress['forecasted_count']==0

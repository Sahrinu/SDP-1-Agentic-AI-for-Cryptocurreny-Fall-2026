"""Credentials stay in memory; local HTTP controls never echo or persist keys."""
import io
import json
import re
import sqlite3
from pathlib import Path
import pytest

from dashboard.credentials import SessionCredentials, load_dashboard_environment, parse_key_payload
from dashboard.__main__ import create_server
from dashboard.service import market_snapshot, recent_news_snapshot

TEST_KEY='unit-test-session-value-not-a-real-key'


def test_news_snapshot_reuse_window():
    from datetime import datetime, timedelta, timezone
    at = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    sample = {'student2_news_articles':[{'title':'BTC news'}],
        'student2_news_observed_at':(at-timedelta(seconds=119)).isoformat()}
    assert recent_news_snapshot(sample, at=at)
    sample['student2_news_observed_at'] = (at-timedelta(seconds=121)).isoformat()
    assert not recent_news_snapshot(sample, at=at)
    sample['student2_news_observed_at'] = (at+timedelta(seconds=1)).isoformat()
    assert not recent_news_snapshot(sample, at=at)
    sample['student2_news_observed_at'] = 'bad timestamp'
    assert not recent_news_snapshot(sample, at=at)


def test_forecast_reuses_recent_news_and_drops_it_after_failed_refetch(local_http,monkeypatch):
    from datetime import datetime, timedelta, timezone
    import time
    import dashboard.__main__ as server_module
    import market_data.live as live

    observed = {'at':datetime.now(timezone.utc)}
    fetched = []
    seen = []
    article = {'title':'BTC news from unit test','evidence_id':'unit-test-article'}
    def collect_snapshot(*args, **kwargs):
        return ({'student2_news_articles':[article],
                 'student2_news_observed_at':observed['at'].isoformat()},
                {'news':{'status':'collected','received_at':observed['at'].isoformat()}})
    def collect_news():
        fetched.append(True)
        raise RuntimeError('Yahoo unavailable in unit test')
    def forecast(data, quote, *, client, on_progress, ledger=None):
        seen.append(data)
        return {'id':'news-test-'+str(len(seen))}
    books = []
    def refresh_betting(data, directory):
        book = {'capture':len(books), 'source':'injected unit test only'}
        books.append(book)
        data['student2_market_observation'] = book
        return {'status':'collected'}
    monkeypatch.setattr(live,'refresh_betting_inputs',refresh_betting)
    monkeypatch.setattr(live,'collect_snapshot',collect_snapshot)
    monkeypatch.setattr(live,'collect_news',collect_news)
    monkeypatch.setattr(live,'reference_quote',lambda:{'source':'unit-test-only'})
    monkeypatch.setattr(server_module,'forecast_snapshot',forecast)

    def run(action):
        code,_,_=local_http('POST','/api/'+action,headers={'X-Demo-Token':local_http.token})
        assert code == 202
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            _,payload,_ = local_http('GET','/api/state')
            state = json.loads(payload)
            if not state['busy']:
                return state
            time.sleep(.005)
        pytest.fail(action+' timed out')

    run('collect')
    state = run('forecast')
    assert not fetched and seen[-1]['student2_news_articles'] == [article]
    assert state['sources']['news']['reused'] is True
    observed['at'] = datetime.now(timezone.utc)-timedelta(minutes=3)
    run('collect')
    state = run('forecast')
    assert len(fetched) == 1 and 'student2_news_articles' not in seen[-1]
    assert state['sources']['news']['status'] == 'unavailable'
    assert len(books) == 2 and seen[-1]['student2_market_observation'] == books[-1]

@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)


def test_project_env_supplies_gemini_key_without_loading_other_settings(tmp_path,monkeypatch,clean_env):
    import os
    key='unit-test-file-value-not-a-real-key'
    (tmp_path/'.env').write_text(
        f'GEMINI_API_KEY={key}\nCOINGECKO_DEMO_API_KEY=unrelated-test-value\n',
        encoding='utf-8')
    monkeypatch.delenv('COINGECKO_DEMO_API_KEY',raising=False)
    server=create_server(8765,root=tmp_path,bind=False)
    server.server_close()
    assert SessionCredentials().resolved_key()==key
    assert os.environ.get('COINGECKO_DEMO_API_KEY') is None
    assert key not in json.dumps(SessionCredentials().public_status())


def test_server_environment_takes_precedence_over_project_env(tmp_path,monkeypatch,clean_env):
    import os
    (tmp_path/'.env').write_text('GEMINI_API_KEY=file-test-value\n',encoding='utf-8')
    monkeypatch.setenv('GOOGLE_API_KEY','host-test-value')
    load_dashboard_environment(tmp_path)
    assert SessionCredentials().resolved_key()=='host-test-value'
    assert os.environ.get('GEMINI_API_KEY') is None

@pytest.fixture
def local_http(tmp_path,clean_env):
    server=create_server(8765,root=tmp_path,bind=False)
    server.server_port=8765
    class Connection:
        def __init__(self,raw):self.raw=raw;self.response=bytearray()
        def makefile(self,*args,**kwargs):return io.BytesIO(self.raw)
        def sendall(self,data):self.response.extend(data)
        def settimeout(self,value):pass
    def call(method,path,body=b'',headers=None):
        h={'Host':'127.0.0.1:8765','Content-Length':str(len(body)),**(headers or {})}
        raw=(method+' '+path+' HTTP/1.0\r\n'+''.join(k+': '+v+'\r\n' for k,v in h.items())+'\r\n').encode()+body
        conn=Connection(raw)
        server.RequestHandlerClass(conn,('127.0.0.1',1),server)
        head,payload=bytes(conn.response).split(b'\r\n\r\n',1)
        return int(head.split()[1]),payload,head
    _,html,_=call('GET','/')
    token=re.search(rb'name="demo-token" content="([^"]+)"',html).group(1).decode()
    call.token=token
    call.root=tmp_path
    yield call
    server.server_close()


def test_session_overrides_environment_and_clears_without_mutating_it(monkeypatch,clean_env):
    monkeypatch.setenv('GOOGLE_API_KEY','environment-test-only')
    credentials=SessionCredentials()
    assert credentials.resolved_key()=='environment-test-only'
    credentials.set_key(TEST_KEY)
    assert credentials.resolved_key()==TEST_KEY
    assert TEST_KEY not in repr(credentials)
    public=json.dumps(credentials.public_status())
    assert TEST_KEY not in public and 'session' in public
    assert SessionCredentials().resolved_key()=='environment-test-only'
    credentials.clear()
    assert credentials.resolved_key()=='environment-test-only'
    assert credentials.public_status()['source']=='environment'

@pytest.mark.parametrize('payload',[b'bad json',b'[]',b'{}',b'{"api_key": 1}',b'{"api_key": ""}',
    b'{"api_key": "key with spaces"}',b'{"api_key":"abc", "extra":"x"}',
    json.dumps({'api_key':'x'*4097}).encode(),b'{"api_key":"line\\nfeed"}'])
def test_invalid_key_payloads_are_rejected_without_echo(payload):
    with pytest.raises(ValueError) as error:parse_key_payload(payload)
    assert TEST_KEY not in str(error.value)


def test_session_http_set_clear_and_no_key_leak_or_file_write(local_http):
    call=local_http
    code,raw,_=call('POST','/api/gemini',json.dumps({'api_key':TEST_KEY}).encode(),{'X-Demo-Token':call.token})
    assert code==200 and TEST_KEY.encode() not in raw
    assert json.loads(raw)['gemini']['source']=='session'
    code,raw,headers=call('GET','/api/state')
    assert code==200 and json.loads(raw)['gemini_configured']
    assert TEST_KEY.encode() not in raw and b'no-store' in headers
    for route in ('/','/app.js','/styles.css','/api/market'):
        code,raw,_=call('GET',route)
        assert code==200 and TEST_KEY.encode() not in raw
    for file in call.root.rglob('*'):
        if file.is_file():assert TEST_KEY.encode() not in file.read_bytes()
    code,raw,_=call('POST','/api/gemini/clear',headers={'X-Demo-Token':call.token})
    assert code==200 and not json.loads(raw)['gemini']['configured']

@pytest.mark.parametrize('headers',[{}, {'X-Demo-Token':'wrong'},
    {'X-Demo-Token':'TOKEN','Origin':'https://untrusted.example'},
    {'X-Demo-Token':'TOKEN','Host':'untrusted.example:8765'}])
def test_credential_route_rejects_cross_origin_or_unauthorized_requests(local_http,headers):
    headers={k:v.replace('TOKEN',local_http.token) for k,v in headers.items()}
    code,raw,_=local_http('POST','/api/gemini',json.dumps({'api_key':TEST_KEY}).encode(),headers)
    assert code==403 and TEST_KEY.encode() not in raw
    _,state,_=local_http('GET','/api/state')
    assert not json.loads(state)['gemini_configured']

@pytest.mark.parametrize('body,headers',[(b'bad json',{}),(b'{}',{}),(b'',{'Content-Length':'99999'}),
    (b'',{'Content-Length':'abc'}),(b'{}',{'Transfer-Encoding':'chunked'})])
def test_bad_http_body_never_updates_configuration(local_http,body,headers):
    code,raw,_=local_http('POST','/api/gemini',body,{'X-Demo-Token':local_http.token,**headers})
    assert code==400
    _,state,_=local_http('GET','/api/state')
    assert not json.loads(state)['gemini_configured']


def test_static_assets_and_traversal_guard(local_http):
    for route,mime in [('/app.js',b'text/javascript'),('/styles.css',b'text/css')]:
        code,body,headers=local_http('GET',route)
        assert code==200 and body and mime in headers
        assert b"script-src 'self';" in headers
        assert b"form-action 'none'" in headers
    assert local_http('GET','/../.env')[0]==404
    assert local_http('GET','/api/gemini')[0]==404
    assert local_http('GET','/api/state',headers={'Host':'untrusted.example'})[0]==403


def test_explicit_key_is_passed_to_gemini_without_touching_environment(monkeypatch,clean_env):
    from google import genai
    from model_clients.demo_gemini import DemoGemini
    import os
    captured={}
    def constructor(**kwargs):captured.update(kwargs);return object()
    monkeypatch.setattr(genai,'Client',constructor)
    client=DemoGemini(api_key=TEST_KEY)
    assert captured['api_key']==TEST_KEY
    assert client.usage==[]
    assert 'GOOGLE_API_KEY' not in os.environ and 'GEMINI_API_KEY' not in os.environ


def test_chart_payload_has_only_actual_collected_ohlcv():
    import pandas as pd
    frame=pd.DataFrame({'open':[100.], 'high':[105.], 'low':[98.], 'close':[102.], 'volume':[24.]},
        index=pd.to_datetime(['2026-11-14'],utc=True))
    data=market_snapshot({'btc_prices':frame},{'btc_prices':{'received_at':'2026-11-15T10:00:00Z'}})
    assert data['candles']==[{'date':'2026-11-14','open':100.,'close':102.,'high':105.,'low':98.,'volume':24.}]
    frame=frame.drop(columns=['volume','high','low'])
    assert 'volume' not in market_snapshot({'btc_prices':frame},{})['candles'][0]
    assert market_snapshot({}, {})['candles']==[]


def test_key_form_has_no_url_submission_fallback(local_http):
    _,html,_=local_http('GET','/')
    assert b'<form id="key-form" method="dialog" autocomplete="off">' in html
    _,script,_=local_http('GET','/app.js')
    assert b'localStorage' not in script and b'sessionStorage' not in script


@pytest.mark.parametrize('stale_source', [None, 'prices', 'book'])
def test_http_progress_is_visible_before_the_forecast_finishes(local_http,monkeypatch,stale_source):
    from datetime import datetime, timedelta, timezone
    import threading
    import time
    import pandas as pd
    import dashboard.__main__ as server_module
    import market_data.live as live
    from agents.registry import CATALOG
    entered,release=threading.Event(),threading.Event()
    captures = []
    book_refreshes = []
    now = datetime.now(timezone.utc)
    def collect(directory, *, input_directory):
        captures.append(directory)
        first = len(captures) == 1
        candle_age = 2 if first and stale_source == 'prices' else 1
        book_age = 6 if first and stale_source == 'book' else 0
        prices = pd.DataFrame({'close': [100.]}, index=pd.to_datetime(
            [now.date() - timedelta(days=candle_age)], utc=True))
        return {'btc_prices': prices, 'student2_markets_observed_at':
                (now - timedelta(minutes=book_age)).isoformat()}, {}
    monkeypatch.setattr(live,'collect_snapshot',collect)
    monkeypatch.setattr(live,'reference_quote',lambda:{'source':'unit-test-only'})
    monkeypatch.setattr(live,'collect_news',lambda:[{'title':'Unit test BTC article'}])
    monkeypatch.setattr(live,'collect_recent_bitcoin',lambda:{})
    def refresh_betting(data, directory):
        book_refreshes.append(data.pop('student2_markets_observed_at', None))
        return {'status':'unavailable','reason':'offline test'}
    monkeypatch.setattr(live,'refresh_betting_inputs',refresh_betting)
    def forecast(data,quote,*,client,on_progress,ledger=None):
        for index,spec in enumerate(CATALOG):
            on_progress({'event':'agent_started','role':spec.name})
            if index==0:
                entered.set()
                assert release.wait(timeout=5)
            result={'agent':spec.name,'role':spec.name,'day':'2026-11-15','p_up':.6,
                'data_missing':False,'evidence':['synthetic unit test only'],'audit':{'passed':True}}
            on_progress({'event':'agent_completed','role':spec.name,'status':'forecasted','result':result})
        on_progress({'event':'aggregation_started'})
        methods={'exchange':.6,'vote':1.,'average':.6}
        on_progress({'event':'aggregation_completed','methods':methods})
        return {'id':'unit-test-forecast','methods':methods}
    monkeypatch.setattr(server_module,'forecast_snapshot',forecast)
    if stale_source:
        assert local_http('POST','/api/collect',headers={'X-Demo-Token':local_http.token})[0]==202
        deadline=time.monotonic()+5
        while json.loads(local_http('GET','/api/state')[1])['busy']:
            assert time.monotonic() < deadline
            time.sleep(.005)
    code,_,_=local_http('POST','/api/forecast',headers={'X-Demo-Token':local_http.token})
    assert code==202
    try:
        assert entered.wait(timeout=5)
        assert len(captures) == {None: 0, 'prices': 2, 'book': 1}[stale_source]
        assert len(book_refreshes) == 1
        _,payload,_=local_http('GET','/api/state')
        state=json.loads(payload)
        assert state['busy'] and state['forecast'] is None
        assert state['activity']['current_agent']=='Chart'
        assert state['activity']['agents'][0]['status']=='running'
        assert state['activity']['evaluated_count']==0
    finally:
        release.set()
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        _,payload,_=local_http('GET','/api/state')
        state=json.loads(payload)
        if not state['busy']:break
        time.sleep(.005)
    assert not state['busy'] and state['activity']['status']=='completed'
    assert state['activity']['evaluated_count']==13
    saved=json.loads((local_http.root/'outputs/dashboard/forecasts/unit-test-forecast.json').read_text())
    assert saved['activity']['status']=='completed' and saved['activity']['evaluated_count']==13


def test_paper_setup_and_manual_order_http_roundtrip(local_http, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import dashboard.__main__ as server_module
    import market_data.live as live
    now=datetime.now(timezone.utc)
    forecast={'id':'http-paper-forecast','mode':'live','created_at':now.isoformat(),
              'methods':{'exchange':.48},'cap_feasible':True,'agents':[],
              'quote':{'price':60000,'retrieved_at':now.isoformat(),'bar_close_time':now.isoformat()},
              'contract':{'prediction_time':now.isoformat(),'target_time':(now+timedelta(hours=24)).isoformat()}}
    class InlineThread:
        def __init__(self,target,args,**kwargs):self.target,self.args=target,args
        def start(self):self.target(*self.args)
    monkeypatch.setattr(server_module.threading,'Thread',InlineThread)
    monkeypatch.setattr(server_module,'forecast_snapshot',lambda *a,**k:dict(forecast))
    monkeypatch.setattr(server_module.ForecastActivity,'finish',lambda self:None)
    monkeypatch.setattr(live,'collect_news',lambda:[])
    monkeypatch.setattr(live,'reference_quote',lambda:forecast['quote'])
    monkeypatch.setattr(live,'refresh_betting_inputs',lambda *a,**k:{'status':'unavailable'})
    headers={'X-Demo-Token':local_http.token}
    settings=json.dumps({'max_forecast_swing':'.2','max_cumulative_loss':'.1','buy_fraction':'.1'}).encode()
    assert local_http('POST','/api/paper/settings',settings)[0]==403
    assert local_http('POST','/api/paper/settings',settings,headers)[0]==400
    assert local_http('POST','/api/forecast',headers=headers)[0]==202
    _,snapshot,_=local_http('GET','/api/state')
    assert json.loads(snapshot)['forecast'], snapshot
    code,body,_=local_http('POST','/api/paper/settings',settings,headers)
    assert code==200 and json.loads(body)['paper_settings']['configured'], body
    assert local_http('POST','/api/paper',b'{"action":"BUY"}',headers)[0]==202
    _,body,_=local_http('GET','/api/state');state=json.loads(body)
    assert state['paper']['trade']['action']=='BUY'
    assert state['paper']['portfolio']['trade_count']==1
    code,portfolio,_=local_http('GET','/api/portfolio')
    assert code==200
    portfolio=json.loads(portfolio)
    assert portfolio['portfolio']['btc']==state['paper']['portfolio']['btc']
    assert portfolio['portfolio']['equity_usd']==state['paper']['portfolio']['equity_usd']
    assert len(portfolio['history'])==1
    assert local_http('POST','/api/paper/settings',settings,headers)[0]==400
    assert local_http('POST','/api/paper',b'{"action":"SELL"}',headers)[0]==202
    _,body,_=local_http('GET','/api/state')
    assert json.loads(body)['paper']['portfolio']['trade_count']==1


@pytest.mark.parametrize('body',[b'[]',b'{"action":"INVALID"}',b'{"action":[]}',b'{"unexpected":1}',b'not json'])
def test_paper_rejects_malformed_manual_requests(local_http,body):
    assert local_http('POST','/api/paper',body,{'X-Demo-Token':local_http.token})[0]==400


def test_portfolio_endpoint_is_read_only_and_needs_no_forecast(local_http):
    code,raw,_=local_http('GET','/api/portfolio')
    value=json.loads(raw)
    assert code==200 and value['status']=='waiting'
    assert value['portfolio']['cash_usd']=='10000' and value['history']==[]
    assert local_http('GET','/api/portfolio',headers={'Host':'untrusted.example'})[0]==403
    with sqlite3.connect(local_http.root/'outputs/dashboard/paper.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM state').fetchone()[0]==0

"""Live boundaries, independent abstentions, and atomic paper risk controls."""
from datetime import datetime,timedelta,timezone
import copy
import math
import pandas as pd
import pytest
from agents import chart,long_term,network,miner
from agents._shared import cutoff_for,daily_prices
from dashboard.service import forecast_snapshot,PaperStore
from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy
from risk.circuit_breaker import CircuitBreaker,constrained_action
from risk.auditor import audit_forecast
from market_data.live import collect_chain,collect_recent_bitcoin,reference_quote

NOW=datetime(2026,11,15,10,tzinfo=timezone.utc)
DAY='2026-11-15'

def prices():
    dates=pd.date_range(end='2026-11-14',periods=1100,tz='UTC')
    return pd.DataFrame({'close':[10000*math.exp(i*.001+.04*math.sin(i/10)) for i in range(1100)]},index=dates)

def quote():
    return {'price':70000,'bar_close_time':(NOW-timedelta(seconds=1)).isoformat(),
            'retrieved_at':NOW.isoformat(),'source':'synthetic unit test only'}

@pytest.mark.parametrize('module',[chart,long_term,network,miner])
def test_missing_abstains(module):
    assert module.forecast(DAY,{})['data_missing']

@pytest.mark.parametrize('module',[chart,long_term])
def test_completed_only_and_future_invariance(module):
    frame=prices()
    view={'btc_prices':frame,'_decision_time':NOW}
    result=module.forecast(DAY,view)
    assert not result['data_missing']
    frame.loc[pd.Timestamp(DAY,tz='UTC')]=1e99
    assert result==module.forecast(DAY,view)
    frame.drop(frame.index[-2],inplace=True)
    assert module.forecast(DAY,view)['data_missing']


def test_intraday_collector_keeps_only_complete_consecutive_candles():
    from urllib.parse import parse_qs,urlsplit
    def rows(interval, *, gap=False):
        step=timedelta(hours=1) if interval=='1h' else timedelta(minutes=1)
        count=25 if interval=='1h' else 61
        result=[]
        for offset in range(-count-1,1):
            if gap and interval=='1h' and offset==-20:
                continue
            opened=NOW+offset*step
            close_time=opened+step-timedelta(milliseconds=1)
            stamp=lambda value:int(value.timestamp()*1000)
            result.append([stamp(opened),'100','102','99','101','10',
                           stamp(close_time),'1000',20,'5','500','0'])
        return result
    def get(url):
        interval=parse_qs(urlsplit(url).query)['interval'][0]
        return rows(interval)
    frames=collect_recent_bitcoin(get,observed_at=NOW)
    assert len(frames['btc_hourly'])==25 and len(frames['btc_minute'])==61
    assert all(frame.index[-1]<NOW for frame in frames.values())
    with pytest.raises(ValueError):
        collect_recent_bitcoin(lambda url:rows(parse_qs(urlsplit(url).query)['interval'][0],gap=True),
                               observed_at=NOW)


def test_chart_blends_daily_hourly_and_minute_without_future_bars():
    hourly=pd.DataFrame({'close':[100+i*.2 for i in range(25)]},
        index=pd.date_range(end=NOW-timedelta(milliseconds=1),periods=25,freq='h',tz='UTC'))
    minute=pd.DataFrame({'close':[114+i*.01 for i in range(61)]},
        index=pd.date_range(end=NOW-timedelta(milliseconds=1),periods=61,freq='min',tz='UTC'))
    view={'btc_prices':prices(),'btc_hourly':hourly,'btc_minute':minute,'_decision_time':NOW}
    combined=chart.forecast(DAY,view)
    daily=chart.forecast(DAY,{'btc_prices':prices(),'_decision_time':NOW})
    assert not combined['data_missing'] and combined['p_up']!=daily['p_up']
    assert any('completed hourly candles' in item for item in combined['evidence'])
    hourly.loc[NOW+timedelta(hours=1),'close']=1e99
    minute.loc[NOW+timedelta(minutes=1),'close']=1e99
    assert chart.forecast(DAY,view)==combined
    minute.index=minute.index-timedelta(minutes=10)
    assert chart.forecast(DAY,view)['data_missing']

@pytest.mark.parametrize('module,key,fields',[(network,'network',{'active_addresses':100,'transactions':1000}),
                                           (miner,'miner',{'hash_rate':900,'fees_btc':10})])
def test_chain_publication_boundary(module,key,fields):
    frame=pd.DataFrame({**fields,'available_at':NOW.isoformat(),'complete':True,'source':'unit-test'},
        index=pd.date_range('2026-11-07',periods=8,tz='UTC'))
    result=module.forecast(DAY,{key:frame,'_decision_time':NOW})
    assert not result['data_missing'] and result['p_up']==.5
    frame['available_at']=(NOW+timedelta(seconds=1)).isoformat()
    assert module.forecast(DAY,{key:frame,'_decision_time':NOW})['data_missing']

@pytest.mark.parametrize('stamp',['2026-11-15T10:00:00','2026-11-14T10:00:00Z'])
def test_live_cutoff_rejects_wrong_contract(stamp):
    with pytest.raises(ValueError):cutoff_for(DAY,{'_decision_time':stamp})


def test_thirteen_roles_return_even_with_unavailable_inputs():
    result=forecast_snapshot({},quote(),decision_time=NOW)
    assert len(result['agents'])==13
    assert result['active_count']==0 and result['methods']['exchange'] is None
    assert all(a['p_up'] is None for a in result['agents'])


def test_live_rule_agents_and_equal_coin_provenance():
    result=forecast_snapshot({'btc_prices':prices()},quote(),decision_time=NOW)
    assert {'Chart','Crash','Long-term','Contrarian'} <= {a['role'] for a in result['agents'] if a['p_up'] is not None}
    assert not result['cap_feasible']
    assert abs(sum(result['weights'].values())-1)<1e-10
    assert result['contract']['target_time']=='2026-11-16T10:00:00+00:00'

@pytest.mark.parametrize('change',[{'price':float('nan')},{'price':True},
    {'bar_close_time':'2026-11-15T09:00:00+00:00'},{'retrieved_at':'2026-11-15T10:01:00+00:00'}])
def test_reference_validation(change):
    q=quote();q.update(change)
    with pytest.raises(ValueError):forecast_snapshot({},q,decision_time=NOW)


def paper_forecast():
    return {'id':'one-decision','created_at':NOW.isoformat(),'mode':'live',
        'methods':{'exchange':.7},'quote':quote(),'cap_feasible':True}


def test_paper_idempotence_and_cost_aware_cap(tmp_path):
    policy=RiskPolicy('.3','.1')  # synthetic offline thresholds
    state=tmp_path/'risk.sqlite3'
    RiskGateway.initialize_state(state,policy=policy,trader=PaperTrader(),actor='test',
        reason='synthetic test',timestamp=NOW-timedelta(minutes=1),previous_forecast='.5')
    store=PaperStore(tmp_path/'paper.db',risk_policy=policy,risk_state_path=state,buy_fraction='.24')
    f=paper_forecast()
    first=store.decide(f,now=NOW)
    assert first['risk']['action']=='BUY'
    assert float(first['risk']['projected_exposure'])<=.25
    assert float(first['portfolio']['total_fees_usd'])>0
    assert PaperStore(tmp_path/'paper.db').decide(f,now=NOW)==first
    f['id']='next';f['methods']['exchange']=.1
    second=store.decide(f,now=NOW+timedelta(seconds=1))
    assert second['breaker']['halted']
    assert second['risk']['action']=='HOLD' and second['portfolio']['btc']==first['portfolio']['btc']
    assert second['trade'] is None


def test_cap_infeasible_blocks_new_buy(tmp_path):
    f=paper_forecast();f['cap_feasible']=False
    result=PaperStore(tmp_path/'paper.db').decide(f,now=NOW)
    assert result['constrained']=='HOLD' and not result['trade']

@pytest.mark.parametrize('mode,age',[('synthetic',0),('live',181),('live',-1)])
def test_paper_rejects_stale_or_nonlive(tmp_path,mode,age):
    f=paper_forecast();f['mode']=mode
    with pytest.raises(ValueError):PaperStore(tmp_path/'paper.db').decide(f,now=NOW+timedelta(seconds=age))


def test_breaker_is_sticky_and_preserves_exits():
    b=CircuitBreaker();b.check(100,.5)
    assert b.check(89,.5)['halted']
    assert b.check(110,.5)['halted']
    assert constrained_action('BUY',halted=True)=='HOLD'
    assert constrained_action('SELL',halted=True)=='SELL'
    b.reset();assert not b.check(100,.5)['halted']


def test_auditor_rejects_bad_evidence_and_future():
    result={'day':DAY,'p_up':.6,'evidence':['test'],'observed_at':(NOW+timedelta(seconds=1)).isoformat()}
    assert not audit_forecast(result,day=DAY,prediction_time=NOW)['passed']
    result['observed_at']='malformed'
    assert not audit_forecast(result,day=DAY,prediction_time=NOW)['passed']


def test_chain_retains_real_receipt_not_old_publication(monkeypatch):
    import market_data.live as live
    monkeypatch.setattr(live,'now',lambda:NOW)
    raw={'data':[{'time':'2026-11-14T00:00:00Z','AdrActCnt':'1','TxCnt':'2','HashRate':'3','FeeTotNtv':'4'},
                 {'time':'2026-11-15T00:00:00Z','AdrActCnt':'9','TxCnt':'9','HashRate':'9','FeeTotNtv':'9'}]}
    result=collect_chain(lambda url:raw)
    assert len(result['network'])==1
    assert result['network'].iloc[0].available_at==NOW.isoformat()
    assert result['miner'].iloc[0].fees_btc==4


def test_funding_millisecond_jitter_does_not_discard_real_settlements():
    from market_data.borrowing import normalize
    day=pd.Timestamp('2026-11-14',tz='UTC')
    funding=[{'symbol':'BTCUSDT','fundingRate':'0.0001','fundingTime':int((day+pd.Timedelta(hours=h)).timestamp()*1000)+1} for h in (0,8,16)]
    oi=[{'symbol':'BTCUSDT','timestamp':int((day+pd.Timedelta(days=1)).timestamp()*1000),'sumOpenInterestValue':'1000000000'}]
    result=normalize(funding,oi,'2026-11-14','2026-11-14',retrieved_at=NOW)
    assert result.iloc[0].complete
    funding[-1]['fundingTime']+=1001
    assert not normalize(funding,oi,'2026-11-14','2026-11-14',retrieved_at=NOW).iloc[0].complete
    funding[-1]['fundingTime']=funding[0]['fundingTime']+1
    assert not normalize(funding,oi,'2026-11-14','2026-11-14',retrieved_at=NOW).iloc[0].complete

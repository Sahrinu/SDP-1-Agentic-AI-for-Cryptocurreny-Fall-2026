"""Offline collector-to-agent as-of and provenance regressions."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from agents.world_economy import forecast
from market_data.macro import align_daily, collect

NOW=datetime(2026,11,15,12,tzinfo=timezone.utc)


@pytest.fixture
def sample_series():
    dates=pd.bdate_range('2026-10-19','2026-11-13')
    return {'dxy':pd.Series([100+i*.1 for i in range(len(dates))],index=dates),
            'treasury_10y':pd.Series([4+i*.01 for i in range(len(dates))],index=dates)}


def frame_for(series,end='2026-11-15'):
    return align_daily(series,'2026-10-19',end)


def test_weekend_uses_friday_and_discloses_assumption(sample_series):
    frame=frame_for(sample_series)
    saturday=forecast('2026-11-14',{'macro':frame})
    sunday=forecast('2026-11-15',{'macro':frame})
    assert not saturday['data_missing'] and not sunday['data_missing']
    assert saturday['p_up']==sunday['p_up']
    assert 'source date 2026-11-13' in sunday['evidence'][0]
    assert 'carried 2 calendar days' in sunday['evidence'][0]
    assert 'assumed' in sunday['evidence'][-1].lower()
    assert 'not verified' in sunday['evidence'][-1].lower()


def test_stale_observation_abstains(sample_series):
    result=forecast('2026-11-20',{'macro':frame_for(sample_series,'2026-11-20')})
    assert result['data_missing'] and result['p_up']==.5
    assert any('freshness' in text.lower() or 'stale' in text.lower() for text in result['evidence'])


def test_future_observation_and_future_availability_are_not_used(sample_series):
    frame=frame_for(sample_series,'2026-11-16')
    before=forecast('2026-11-15',{'macro':frame})
    frame.loc['2026-11-16',['dxy','treasury_10y']]=999999
    assert forecast('2026-11-15',{'macro':frame})==before
    frame=frame_for(sample_series)
    expected=forecast('2026-11-12',{'macro':frame})
    for field in ('dxy','treasury_10y'):
        frame.loc['2026-11-13':,field+'_available_at']='2026-11-17T00:00:00Z'
    result=forecast('2026-11-15',{'macro':frame})
    assert not result['data_missing'] and result['p_up']==expected['p_up']
    assert 'source date 2026-11-12' in result['evidence'][0]


def test_tampered_assumed_availability_is_rejected(sample_series):
    frame=frame_for(sample_series)
    frame.loc['2026-11-13','dxy_available_at']='2026-11-14T00:01:00Z'
    result=forecast('2026-11-15',{'macro':frame})
    assert result['data_missing']
    assert any('invalid' in text.lower() for text in result['evidence'])


@pytest.mark.parametrize('session',['2026-11-06','2026-11-09','2026-11-10',
                                    '2026-11-11','2026-11-12','2026-11-13'])
def test_conflict_in_any_lookback_session_abstains(sample_series,session):
    frame=frame_for(sample_series)
    for column in ('dxy_observed_on','dxy_available_at'):
        frame.loc['2026-11-15',column]=frame.loc[session,column]
    frame.loc['2026-11-15','dxy']=frame.loc[session,'dxy']+100
    result=forecast('2026-11-15',{'macro':frame})
    assert result['data_missing']
    assert any('conflicting' in text.lower() for text in result['evidence'])


def test_live_snapshot_uses_collection_completion_and_not_publication(tmp_path,monkeypatch,sample_series):
    from market_data import live, macro
    import agents.specialists.news_specialist as news
    readings=iter((NOW-timedelta(seconds=10),NOW))
    def downloader(symbol,**kwargs):
        return pd.DataFrame({'Close':sample_series['dxy' if symbol=='DX-Y.NYB' else 'treasury_10y']})
    saved=collect('2026-10-19','2026-11-14',output=tmp_path/'download'/'macro.csv',
                  downloader=downloader,now=NOW,clock=lambda:next(readings))
    def fake_collect(*args,**kwargs):
        assert kwargs['clock'] is live.now
        return saved.copy()
    def unavailable(*args,**kwargs):
        raise RuntimeError('offline mock')
    monkeypatch.setattr(macro,'collect',fake_collect)
    monkeypatch.setattr(live,'now',lambda:NOW)
    monkeypatch.setattr(live,'collect_bitcoin',unavailable)
    monkeypatch.setattr(live,'collect_chain',unavailable)
    monkeypatch.setattr(live,'get_json',unavailable)
    monkeypatch.setattr(news,'_fetch_recent_btc_news',unavailable)
    monkeypatch.delenv('COINGECKO_DEMO_API_KEY',raising=False)
    data,status=live.collect_snapshot(tmp_path/'snapshot')
    frame=data['macro']
    assert status['macro']['status']=='collected'
    assert set(frame.availability_basis)=={'retrieved_vintage_live_only'}
    assert set(frame.dxy_available_at)=={NOW.isoformat()}
    assert set(frame.treasury_10y_available_at)=={NOW.isoformat()}
    assert frame.iloc[-1].dxy_observed_on=='2026-11-13'
    before=forecast('2026-11-15',{'macro':frame,'_decision_time':(NOW-timedelta(seconds=1)).isoformat()})
    at=forecast('2026-11-15',{'macro':frame,'_decision_time':NOW.isoformat()})
    assert before['data_missing'] and not at['data_missing']
    assert 'retrieval completion' in at['evidence'][-1].lower()
    assert 'not provider publication' in at['evidence'][-1].lower()

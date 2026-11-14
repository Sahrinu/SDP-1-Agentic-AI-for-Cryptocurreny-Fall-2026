"""Offline forecasting tests: fabricated evidence and injected model responses."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
import pandas as pd

from student2.agents import market_evidence_specialist as evidence
from student2.agents import market_evidence_with_prices as enriched
from student2.agents import threshold_market_evidence as threshold
from student2.integration_adapter import Student2Adapter
from tests.test_market_evidence_specialist import PRED, TARGET, observation, response
from tests.test_market_price_context import PRED as PRICE_PRED, context, observation as price_observation
from tests.test_runner_abstentions import runner
from tests.test_limitless_live_reader import frame, market, contract as strict_contract, NOW
from student2.agents import limitless_live as live


def contract():
    return {'prediction_time':PRED, 'target_time':TARGET, 'price_source':'binance:BTC/USDT',
            'reference_price_definition':'spot_price', 'tie_definition':'down'}


def inputs():
    return {'student2_forecast':contract(), 'student2_market_observation':observation(),
            'student2_markets_observed_at':PRED}


def test_mismatched_valid_market_produces_independent_mock_estimate():
    prompts=[]
    def model(prompt):
        prompts.append(prompt)
        return {**response(),'explanation':'Assumption: short-term direction persists with uncertainty.'}
    result=Student2Adapter('betting_odds',model_client=model).forecast(PRED.date().isoformat(),inputs())
    assert result['p_up']==pytest.approx(.52)
    assert result['p_up']!=observation()['quote']['yes_payout_midpoint']
    assert result['data_status']=='sufficient_data'
    assert 'market-book-001' in result['evidence']
    assert any('windows differ' in s for s in result['limitations'])
    assert any('tie handling differ' in s for s in result['limitations'])
    assert 'assumptions' in prompts[0].lower()
    assert 'not by itself a reason' in prompts[0]
    assert 'Do not invent observed' in prompts[0]
    assert 'Never copy or mechanically rescale' in prompts[0]


def test_prompt_identifies_midpoint_as_payout_price_not_probability():
    features=enriched.prepare(price_observation(),context(),PRICE_PRED)
    instructions=evidence.build_prompt(features).split('\n',1)[0].lower()
    assert 'yes payout midpoint' in instructions
    assert 'payout price for the stated contract' in instructions
    assert 'not an observed conditional probability' in instructions
    assert 'not necessarily a calibrated probability' in instructions


def test_prompt_identifies_volatility_horizon_and_lookback():
    features=enriched.prepare(price_observation(),context(),PRICE_PRED)
    instructions=evidence.build_prompt(features).split('\n',1)[0].lower()
    assert 'realized_volatility_24h' in instructions
    assert 'realized_volatility_7d_scaled_to_24h' in instructions
    assert '24-hour' in instructions
    assert 'not annualized' in instructions
    assert 'sqrt(24)' in instructions
    assert '7d denotes the historical lookback' in instructions


def test_invalid_book_does_not_call_model():
    for mutation in ('stale','tampered','expired','oracle','chart','created_after'):
        data=inputs();obs=data['student2_market_observation'];calls=[]
        if mutation=='stale':obs['raw_book_event']['timestamp']=(PRED-timedelta(seconds=31)).isoformat()
        if mutation=='tampered':obs['quote']['yes_payout_midpoint']=.9
        if mutation=='expired':obs['market']['expired']=True
        if mutation=='oracle':obs['market']['priceOracleMetadata']={'ticker':'ETHUSDT'}
        if mutation=='chart':obs['market']['metadata']={'chart':{'source':'other'}}
        if mutation=='created_after':obs['market']['createdAt']=(PRED+timedelta(seconds=1)).isoformat()
        result=Student2Adapter('betting_odds',model_client=lambda p:calls.append(p)).forecast(PRED.date().isoformat(),data)
        assert result['p_up'] is None
        assert result['data_status']=='insufficient_data'
        assert calls==[]


def test_book_and_injected_callable_required_no_network():
    data=inputs()
    assert Student2Adapter('betting_odds').forecast(PRED.date().isoformat(),data)['p_up'] is None
    data.pop('student2_market_observation')
    calls=[]
    assert Student2Adapter('betting_odds',lambda p:calls.append(p)).forecast(PRED.date().isoformat(),data)['p_up'] is None
    assert calls==[]


def test_runner_uses_repaired_adapter_only_after_matching_settlement():
    data=inputs();calls=[]
    def model(prompt):
        calls.append(prompt)
        return response()
    adapter=Student2Adapter('betting_odds',model_client=model)
    c=data['student2_forecast']
    settlement={k:c[k] for k in ('price_source','reference_price_definition','tie_definition')}
    settlement['close_times']={str(PRED.date()):PRED.isoformat(),str(TARGET.date()):TARGET.isoformat()}
    with TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'data').mkdir()
        pd.DataFrame({'date':[str(PRED.date()),str(TARGET.date())],'close':[100,110]}).to_csv(root/'data/btc_prices.csv',index=False)
        with patch.object(runner,'load_agents',return_value={}):
            forecasts,_,_,summary=runner.run(str(PRED.date()),str(TARGET.date()),0,root/'data',root/'agents',root/'output',
                student2_adapters={'Betting Odds Specialist':adapter},
                student2_inputs={str(PRED.date()):data},settlement_contract=settlement)
        assert forecasts.iloc[0]['p_Betting Odds Specialist']==pytest.approx(.52)
        assert forecasts.iloc[0]['average']==pytest.approx(.52)
        assert summary.set_index('method').loc['average','evaluated_days']==1
    assert len(calls)==1
    error=runner.student2_window_error(pd.Timestamp(PRED.date()),pd.Timestamp(TARGET.date()),data,{})
    forecasts,abstentions=runner.evaluate_agents({'Betting Odds Specialist':adapter},{'Betting Odds Specialist'},
                                                  pd.Timestamp(PRED.date()),data,error)
    assert not forecasts and 'Betting Odds Specialist' in abstentions
    assert len(calls)==1


def test_runner_abstains_for_invalid_book_without_calling_model():
    data=inputs();data['student2_market_observation']['quote']['yes_payout_midpoint']=.99
    calls=[];adapter=Student2Adapter('betting_odds',model_client=lambda p:calls.append(p))
    forecasts,abstentions=runner.evaluate_agents({'Betting Odds Specialist':adapter},{'Betting Odds Specialist'},
                                                  pd.Timestamp(PRED.date()),data,None)
    assert not forecasts and 'Betting Odds Specialist' in abstentions
    assert calls==[]


def test_midpoint_equal_model_output_is_allowed_with_valid_explanation():
    midpoint=observation()['quote']['yes_payout_midpoint']
    result=Student2Adapter('betting_odds',lambda p:{**response(),'probability_up':midpoint}).forecast(PRED.date().isoformat(),inputs())
    assert result['p_up'] == pytest.approx(midpoint)
    assert result['data_status'] == 'sufficient_data'
    assert response()['target_analysis'] in result['evidence'][0]
    assert response()['contract_differences'] in result['evidence'][0]
    assert response()['assumptions'] in result['evidence'][0]
    assert 'Requested target:' in result['evidence'][0]
    assert PRED.isoformat() in result['evidence'][0]
    assert 'Observed market:' in result['evidence'][0]


def test_live_quote_inspection_never_sets_team_probability():
    m=market();event=frame();quote=live.read_book_event(event,m,NOW)
    class Clock(live.datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    with patch.object(live,'datetime',Clock):
        report=live.observe_market(fetch_page=lambda *a:{'data':[m],'totalMarketsCount':1},
            capture=lambda _: (quote,event),contract=strict_contract())
    assert report['observation_status']=='book_observed'
    assert report['quote_inspection']['yes_payout_price']==pytest.approx(.63)
    assert report['team_probability_up'] is None
    assert report['data_status']=='insufficient_data'


def test_rounded_midpoint_is_allowed_with_valid_explanation():
    obs=threshold_observation()
    assert obs['quote']['yes_payout_midpoint']!=.15  # Binary floating-point representation.
    result=evidence.forecast_from_market_evidence(obs,prediction_time='2026-11-14T20:00:01Z',
        target_time='2026-11-15T20:00:01Z',price_source='binance:BTC/USDT',
        reference_price_definition='spot_price',model_call=lambda p:{**response(),'probability_up':.15})
    assert result.probability_up == pytest.approx(.15)
    assert result.data_status == 'sufficient_data'


def test_price_backed_model_uses_mismatch_limitations_and_allows_equal_midpoint():
    features=enriched.prepare(price_observation(),context(),PRICE_PRED)
    assert any('windows differ' in s for s in features['limitations'])
    assert any('tie handling differ' in s for s in features['limitations'])
    midpoint=features['evidence'][0]['yes_payout_midpoint']
    payload={**response(),'probability_up':midpoint,'evidence_ids':['market-book-001']}
    result=enriched.evaluate(features,lambda p:payload)
    assert result.probability_up == pytest.approx(midpoint)
    assert response()['target_analysis'] in result.explanation
    assert response()['contract_differences'] in result.explanation
    assert response()['assumptions'] in result.explanation


@pytest.mark.parametrize('field',['target_analysis','contract_differences','assumptions'])
@pytest.mark.parametrize('invalid',[None,'   '])
def test_required_explanation_sections_reject_missing_or_blank(field,invalid):
    payload=response()
    if invalid is None:del payload[field]
    else:payload[field]=invalid
    result=Student2Adapter('betting_odds',lambda p:payload).forecast(PRED.date().isoformat(),inputs())
    assert result['p_up'] is None
    assert result['data_status']=='insufficient_data'
    features=enriched.prepare(price_observation(),context(),PRICE_PRED)
    with pytest.raises(ValueError):enriched.evaluate(features,lambda p:payload)


def threshold_observation():
    """A fabricated group/book following the documented threshold rule format."""
    child={'id':42,'groupId':100,'slug':'synthetic-threshold','title':'\u2191 88,000',
           'description':threshold._RULE,'expirationTimestamp':1794805200000,
           'tokens':{'yes':'YES','no':'NO'},'tradeType':'clob','marketType':'group',
           'automationType':'manual','status':'FUNDED','expired':False}
    parent={'id':100,'title':'What price will Bitcoin hit November 9-15?',
            'marketType':'group','outcomeTokens':['Yes','No'],'status':'FUNDED','expired':False,
            'expirationTimestamp':1794805200000,'markets':[deepcopy(child)]}
    when='2026-11-14T20:00:00+00:00'
    event={'marketSlug':child['slug'],'timestamp':when,'version':2,'orderbook':
           {'tokenId':'YES','bids':[{'price':.1,'size':2000000}],'asks':[{'price':.2,'size':3000000}]}}
    return {'mode':'live_read_only_observation','observation_status':'book_observed',
            'market':child,'parent_market':parent,'raw_book_event':event,
            'quote':threshold.read_threshold_book(event,child,when)}


def test_valid_threshold_evidence_does_not_force_abstention():
    obs=threshold_observation();calls=[]
    def mock(prompt):
        calls.append(prompt)
        return {**response(),'explanation':'Assumption: threshold odds inform the direction with uncertainty.'}
    result=evidence.forecast_from_market_evidence(obs,prediction_time='2026-11-14T20:00:01Z',
        target_time='2026-11-15T20:00:01Z',price_source='binance:BTC/USDT',
        reference_price_definition='spot_price',model_call=mock)
    assert result.probability_up==pytest.approx(.52)
    assert result.probability_up!=obs['quote']['yes_payout_midpoint']
    assert any('threshold-hit' in s for s in result.limitations)
    assert '"market_threshold": 88000' in calls[0]
    assert '"market_event": "any candle high reaches threshold"' in calls[0]


def test_threshold_with_future_event_window_can_be_evidence():
    obs=threshold_observation()
    when='2026-11-08T20:00:00+00:00'
    obs['raw_book_event']['timestamp']=when
    obs['quote']=threshold.read_threshold_book(obs['raw_book_event'],obs['market'],when)
    result=evidence.forecast_from_market_evidence(obs,prediction_time='2026-11-08T20:00:01Z',
        target_time='2026-11-09T20:00:01Z',price_source='binance:BTC/USDT',
        reference_price_definition='spot_price',model_call=lambda p:response())
    assert result.probability_up==pytest.approx(.52)
    assert any('windows differ' in s for s in result.limitations)


@pytest.mark.parametrize('mutation',['rules','ambiguous_rules','parent','parent_status','duplicate_slug',
                                     'duplicate_token','mixed_token_types','parent_oracle','member_chart',
                                     'expiry','token','missing_token','stale','depth'])
def test_invalid_threshold_evidence_never_reaches_model(mutation):
    obs=threshold_observation();calls=[]
    if mutation=='rules':obs['market']['description']='Title alone is not a resolution rule'
    if mutation=='ambiguous_rules':
        obs['market']['description']+=' Extra exception.'
        obs['parent_market']['markets'][0]['description']=obs['market']['description']
    if mutation=='parent':obs['parent_market']['id']=999
    if mutation=='parent_status':obs['parent_market']['markets'][0]['expired']=True
    if mutation=='mixed_token_types':
        obs['market']['tokens']={'yes':1,'no':'1'}
        obs['parent_market']['markets'][0]['tokens']={'yes':1,'no':'1'}
    if mutation=='parent_oracle':obs['parent_market']['priceOracleMetadata']={'ticker':'ETHUSDT'}
    if mutation=='member_chart':obs['parent_market']['markets'][0]['metadata']={'chart':{'source':'other'}}
    if mutation in ('duplicate_slug','duplicate_token'):
        other=deepcopy(obs['market']);other['id']=43
        if mutation=='duplicate_slug':other['tokens']={'yes':'YES2','no':'NO2'}
        else:other['slug']='other-threshold'
        obs['parent_market']['markets'].append(other)
    if mutation=='expiry':obs['market']['expired']=True
    if mutation=='token':obs['raw_book_event']['orderbook']['tokenId']='NO'
    if mutation=='missing_token':del obs['raw_book_event']['orderbook']['tokenId']
    if mutation=='stale':obs['raw_book_event']['timestamp']='2026-11-14T19:59:00Z'
    if mutation=='depth':obs['raw_book_event']['orderbook']['bids']=[]
    result=evidence.forecast_from_market_evidence(obs,prediction_time='2026-11-14T20:00:01Z',
        target_time='2026-11-15T20:00:01Z',price_source='binance:BTC/USDT',
        reference_price_definition='spot_price',model_call=lambda p:calls.append(p))
    assert result.probability_up is None
    assert result.data_status=='insufficient_data'
    assert calls==[]

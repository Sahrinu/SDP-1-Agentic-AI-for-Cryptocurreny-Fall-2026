"""Real response shapes and strict failures at all model-facing boundaries."""
import json
from types import SimpleNamespace
import pytest
from model_clients.structured import parse_model_object
from agents.specialists.market_evidence_specialist import ModelEstimate, forecast_from_market_evidence
from agents.specialists.market_evidence_with_prices import evaluate, prepare
from agents.specialists.news_specialist import validate_news_specialist_result
from tests.test_market_evidence_specialist import observation, response, PRED, TARGET
from tests.test_market_price_context import context, observation as priced_observation, PRED as PRICE_PRED


@pytest.mark.parametrize('wrap', [
    lambda x:x, lambda x:json.dumps(x), lambda x:'```json\n'+json.dumps(x)+'\n```',
    lambda x:SimpleNamespace(content=json.dumps(x)),
    lambda x:SimpleNamespace(parsed=ModelEstimate.model_validate(x),text=None),
    lambda x:SimpleNamespace(content=[{'type':'reasoning','text':'not JSON'},
                                     {'type':'text','text':json.dumps(x)}]),
])
def test_market_model_response_shapes(wrap):
    result=forecast_from_market_evidence(observation(),prediction_time=PRED,target_time=TARGET,
        price_source='binance:BTC/USDT',reference_price_definition='spot_price',
        model_call=lambda prompt:wrap(response()))
    assert result.probability_up==.52


@pytest.mark.parametrize('payload', ['',None,'[]','{"p":NaN}','{"p":1,"p":2}',
    'Some prose {"p":0.5}', '{"p":0.5} {"p":0.6}', '{"p":',
    SimpleNamespace(content=[{'type':'reasoning','text':'{"p":0.5}'}])])
def test_malformed_or_ambiguous_output_rejected(payload):
    with pytest.raises(ValueError):parse_model_object(payload)


@pytest.mark.parametrize('value',[True,'0.52',float('nan'),float('inf'),-0.1,1.1])
def test_invalid_probability_is_not_repaired(value):
    with pytest.raises(ValueError):ModelEstimate.model_validate({**response(),'probability_up':value})


def test_enriched_path_accepts_actual_json_text_bridge():
    features=prepare(priced_observation(),context(),PRICE_PRED)
    value=evaluate(features,lambda prompt:json.dumps(response()))
    assert value.probability_up==.52


def test_real_gemini_agent_spelling_preserves_registered_identity():
    value=validate_news_specialist_result(dict(agent='news_specialist',prediction_time=PRED,
        target_time=TARGET,probability_up=.4,explanation='Evidence-based estimate',
        data_status='sufficient_data',evidence_ids=['a'],limitations=[]),{'a'})
    assert value.agent=='News Specialist'


def test_native_gemini_structured_schema_usage_and_raw_recording(monkeypatch):
    from google import genai
    from model_clients.demo_gemini import DemoGemini
    from agents.schemas import NewsSpecialistResult
    saved={}
    payload=dict(agent='News Specialist',prediction_time=PRED.isoformat(),target_time=TARGET.isoformat(),
        probability_up=.4,explanation='Unit-test response only',data_status='sufficient_data',
        evidence_ids=['article'],limitations=[])
    class Client:
        def __init__(self,**kwargs):
            saved['options']=kwargs;self.models=self
        def generate_content(self,**kwargs):
            saved['request']=kwargs
            return SimpleNamespace(parsed=None,text=json.dumps(payload),response_id='test-response-id',
                usage_metadata=SimpleNamespace(prompt_token_count=10,candidates_token_count=20))
        def close(self):saved['closed']=True
    monkeypatch.setattr(genai,'Client',Client)
    model=DemoGemini(api_key='unit-test-value-not-a-real-key',model='test-model')
    result=model.with_structured_output(NewsSpecialistResult).invoke([SimpleNamespace(content='test prompt')])
    assert result.probability_up==.4
    assert saved['request']['config']['response_schema'] is NewsSpecialistResult
    assert saved['options']['http_options']['retry_options']['attempts']==1
    assert model.usage[0]['input_tokens']==10
    assert model.responses[0]['response_id']=='test-response-id'
    assert 'unit-test-value-not-a-real-key' not in json.dumps(model.responses+model.usage)
    model.close();assert saved['closed']

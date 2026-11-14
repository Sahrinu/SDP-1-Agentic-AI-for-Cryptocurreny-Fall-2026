"""Fabricated inputs and mocked model results; no real forecasting calls."""
import copy
import importlib.util
import sys
import unittest
from datetime import timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Reuse the reader's synthetic test factories, never archived live predictions.
spec=importlib.util.spec_from_file_location('book_test_inputs',Path(__file__).with_name('test_limitless_live_reader.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
from student2.agents import market_evidence_specialist as model

PRED=fixtures.NOW
TARGET=PRED+timedelta(hours=24)


def observation():
    market=fixtures.market('50-50');market.pop('synthetic')
    event=fixtures.frame()
    quote=fixtures.live.read_book_event(event,market,PRED)
    return dict(mode='live_read_only_observation',observation_status='book_observed',
                market=market,raw_book_event=event,quote=quote)


def response():
    return dict(probability_up=.52,data_status='sufficient_data',explanation='Mock estimate: evidence window differs.',
                target_analysis='The target is a strict BTC price increase over the specified 24 hours.',
                contract_differences='The market window and split tie differ from the target.',
                assumptions='Assume recent directional information persists, with substantial uncertainty.',
                evidence_ids=['market-book-001'],limitations=['Mock response only.'])


def run(obs=None,call=None,**kw):
    args=dict(prediction_time=PRED,target_time=TARGET,price_source='binance:BTC/USDT',reference_price_definition='spot_price',model_call=call)
    args.update(kw)
    return model.forecast_from_market_evidence(observation() if obs is None else obs,**args)


class EvidenceTests(unittest.TestCase):
    def test_supported_different_contract_is_evidence_not_copied(self):
        result=run(call=lambda prompt:response())
        self.assertEqual(result.probability_up,.52)
        self.assertIn('uncalibrated',result.explanation)
        self.assertNotEqual(result.probability_up,.63)

    def test_prompt_discloses_mismatch_and_missing_prices(self):
        prompts=[]
        def call(prompt):prompts.append(prompt);return response()
        run(call=call)
        self.assertIn('"market_tie_rule": "50-50"',prompts[0])
        self.assertIn('"window_matches_target": false',prompts[0])
        self.assertIn('"reference_matches_target": false',prompts[0])
        self.assertIn('not an equivalent target',prompts[0])
        self.assertIn('volatility history are not supplied',prompts[0])

    def test_no_implicit_model(self):
        self.assertIsNone(run().probability_up)

    def test_old_snapshot_rejected_before_model(self):
        calls=[]
        result=run(prediction_time=PRED+timedelta(minutes=6),target_time=TARGET+timedelta(minutes=6),call=lambda p:calls.append(p))
        self.assertIsNone(result.probability_up);self.assertEqual(calls,[])

    def test_future_snapshot_rejected(self):
        result=run(prediction_time=PRED-timedelta(seconds=1),target_time=TARGET-timedelta(seconds=1),call=lambda p:response())
        self.assertIsNone(result.probability_up)

    def test_wrong_interval_rejected(self):
        self.assertIsNone(run(target_time=TARGET+timedelta(seconds=1),call=lambda p:response()).probability_up)

    def test_synthetic_flag_not_hidden(self):
        for level in ('market','top'):
            obs=observation()
            (obs['market'] if level=='market' else obs)['synthetic']=True
            self.assertIsNone(run(obs,lambda p:response()).probability_up)

    def test_tampered_quote_rejected(self):
        obs=observation();obs['quote']['yes_payout_midpoint']=.99
        self.assertIsNone(run(obs,lambda p:response()).probability_up)

    def test_failed_capture_rejected(self):
        obs=observation();obs['observation_status']='failed'
        self.assertIsNone(run(obs,lambda p:response()).probability_up)

    def test_hallucinated_citation_rejected(self):
        r=response();r['evidence_ids']=['fake']
        self.assertIsNone(run(call=lambda p:r).probability_up)

    def test_missing_citation_rejected(self):
        r=response();r['evidence_ids']=[]
        self.assertIsNone(run(call=lambda p:r).probability_up)

    def test_bad_probability_rejected(self):
        for p in (-.1,1.1,float('nan')):
            r=response();r['probability_up']=p
            self.assertIsNone(run(call=lambda prompt:r).probability_up)

    def test_model_can_abstain(self):
        r=response();r.update(probability_up=None,data_status='insufficient_data')
        self.assertIsNone(run(call=lambda p:r).probability_up)

    def test_exception_text_not_exposed(self):
        def fail(p):raise RuntimeError('secret-key')
        r=run(call=fail)
        self.assertNotIn('secret-key',r.model_dump_json())

    def test_inputs_preserved(self):
        obs=observation();before=copy.deepcopy(obs)
        run(obs,lambda p:response())
        self.assertEqual(obs,before)

    def test_adapter_requires_configuration(self):
        r=model.MarketEvidenceAdapter().forecast('2026-11-13',{})
        self.assertIsNone(r['p_up']);self.assertTrue(r['data_missing'])

    def test_adapter_shape_and_target(self):
        r=model.MarketEvidenceAdapter(model_call=lambda p:response()).forecast('2026-11-13',{
            'student2_forecast':dict(prediction_time=PRED,target_time=TARGET,price_source='binance:BTC/USDT',reference_price_definition='spot_price',tie_definition='down'),
            'student2_market_observation':observation()})
        self.assertEqual(r['p_up'],.52);self.assertFalse(r['data_missing'])
        self.assertEqual(r['target_time'],TARGET.isoformat())


if __name__=='__main__':unittest.main()

"""Offline discovery tests; all provider responses and books are synthetic."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from tests.test_limitless_live_reader import NOW, frame, market
from student2.agents import limitless_live as live
from student2.agents import limitless_targeted_discovery as discovery
from student2.agents import binance_raw_context
from student2.agents import market_evidence_specialist as evidence
from student2.agents import market_evidence_with_prices as enriched
from student2.verification import run_market_price_demo as demo


class Clock(live.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class TargetedDiscoveryTests(unittest.TestCase):
    def test_changing_totals_allow_bounded_discovery_and_fresh_detail(self):
        candidate = market('50-50')
        calls = []
        def page(number, limit, timeout):
            calls.append((number, limit, timeout))
            return {'data': [candidate] if number == 1 else [{'id': 99, 'slug': 'other'}],
                    'totalMarketsCount': 100 if number == 1 else 101}
        selected, evidence = discovery.discover(page, lambda slug, timeout: copy.deepcopy(candidate),
                                                 now=NOW, max_pages=2)
        self.assertEqual(selected, candidate)
        self.assertFalse(evidence['search_exhaustive'])
        self.assertEqual(len(evidence['pages']), 2)
        self.assertEqual([c[0] for c in calls], [1, 2])
        self.assertTrue(all(c[1] == 25 and 0 < c[2] <= 15 for c in calls))

    def test_duplicate_or_conflicting_candidate_identity_rejected(self):
        candidate = market()
        for duplicate in (copy.deepcopy(candidate), dict(candidate, slug='different-slug'),
                          dict(candidate, id=77)):
            with self.subTest(duplicate=duplicate):
                def page(n, limit, timeout):
                    return {'data': [candidate] if n == 1 else [duplicate], 'totalMarketsCount': 2}
                with self.assertRaisesRegex(ValueError, 'Duplicate or conflicting'):
                    discovery.discover(page, lambda *a: self.fail('Detail must not be fetched'), now=NOW, max_pages=2)

    def test_inactive_or_invalid_listing_candidate_rejected(self):
        for change in ({'expired': True}, {'status': 'RESOLVED'}, {'description': 'BTC daily without rules'},
                       {'startAt': '2026-11-13T18:00:00Z'}, {'tradeType': 'amm'},
                       {'tokens': {'yes': 'same', 'no': 'same'}},
                       {'priceOracleMetadata': {'ticker': 'ETHUSDT'}}):
            with self.subTest(change=change):
                invalid = dict(market(), **change)
                with self.assertRaisesRegex(ValueError, 'No supported'):
                    discovery.discover(lambda *a: {'data': [invalid]}, lambda *a: self.fail('No detail'),
                                       now=NOW, max_pages=1)

    def test_conflicting_or_invalid_detail_rejected(self):
        candidate = market()
        variants = [dict(candidate, id=2), dict(candidate, slug='other'),
                    dict(candidate, expired=True), dict(candidate, description='bad rules'),
                    dict(candidate, tokens={'yes': 'different', 'no': 'NO_TOKEN'}),
                    dict(candidate, expirationTimestamp=0)]
        for bad in variants:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    discovery.discover(lambda *a: {'data': [candidate]}, lambda *a: bad,
                                       now=NOW, max_pages=1)

    def test_bound_and_no_match_do_not_fetch_detail(self):
        calls=[]
        def page(number, limit, timeout):
            calls.append(number)
            return {'data': [{'id': number, 'slug': 'not-a-daily'}], 'totalMarketsCount': 1000}
        with self.assertRaisesRegex(ValueError, 'bounded search'):
            discovery.discover(page, lambda *a: self.fail('Unexpected detail'), now=NOW, max_pages=3)
        self.assertEqual(calls, [1, 2, 3])
        for invalid in (0, 6, True):
            with self.assertRaises(ValueError): discovery.discover(page, now=NOW, max_pages=invalid)

    def test_request_failure_reports_only_safe_status_and_location(self):
        responses=[b'Public market request failed: HTTP 403\r\n', b'provider-secret-body']
        for stderr in responses:
            with self.subTest(stderr=stderr), \
                 patch.object(discovery.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',stderr)):
                with self.assertRaises(discovery.DiscoveryRequestError) as caught:
                    discovery._fetch_page(2,25,3)
                self.assertIn('/markets/active page 2',str(caught.exception))
                self.assertNotIn('provider-secret',str(caught.exception))
                if b'403' in stderr:self.assertIn('HTTP 403',str(caught.exception))

    def test_reader_uses_detail_and_rejects_stale_stream(self):
        candidate=market(); event=frame()
        quote=live.read_book_event(event,candidate,NOW)
        with patch.object(live,'datetime',Clock):
            report=live.observe_market(targeted_market=candidate,capture=lambda _: (quote,event))
        self.assertEqual(report['observation_status'],'book_observed')
        self.assertEqual(report['listing_source'],'bounded_targeted_discovery')
        self.assertIsNone(report['team_probability_up'])
        stale=copy.deepcopy(event);stale['timestamp']=(NOW-timedelta(seconds=31)).isoformat()
        with patch.object(live,'datetime',Clock):
            report=live.observe_market(targeted_market=candidate,capture=lambda _: (quote,stale))
        self.assertEqual(report['observation_status'],'failed')
        self.assertEqual(report['stage'],'book_stream')
        self.assertIsNone(report['team_probability_up'])

    def test_reader_rejects_invalid_detail_before_stream(self):
        candidate=market();candidate['expired']=True
        with patch.object(live,'datetime',Clock):
            report=live.observe_market(targeted_market=candidate,capture=lambda _: self.fail('No stream'))
        self.assertEqual(report['observation_status'],'failed')
        self.assertEqual(report['stage'],'market_listing')


POWERSHELL=shutil.which('powershell') or shutil.which('pwsh')
HELPER=Path(__file__).resolve().parents[1]/'student2'/'verification'/'get_limitless_public.ps1'
HARNESS=r'''param($Helper,$Kind)
function Invoke-RestMethod {
    param($Uri,$TimeoutSec,$ErrorAction)
    if ($Kind -eq 'page') { return [pscustomobject]@{data=@([pscustomobject]@{id=1;slug='btc-daily'});totalMarketsCount=1} }
    return [pscustomobject]@{id=1;slug='btc-daily'}
}
if ($Kind -eq 'page') { & $Helper -Page 1 -TimeoutSeconds 3 }
else { & $Helper -Slug 'btc-daily' -TimeoutSeconds 3 }
'''


@unittest.skipUnless(POWERSHELL,'PowerShell unavailable')
class PowerShellTransportTests(unittest.TestCase):
    def test_mocked_page_and_detail_are_json_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'mock.ps1';script.write_text(HARNESS,encoding='utf-8')
            for kind in ('page','detail'):
                with self.subTest(kind=kind):
                    result=subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                        '-File',str(script),str(HELPER),kind],capture_output=True,check=True,timeout=15)
                    value=json.loads(result.stdout.decode('utf-8-sig'))
                    self.assertEqual(value['data'][0]['id'] if kind=='page' else value['id'],1)


class DemoIntegrationTests(unittest.TestCase):
    def test_selected_detail_reaches_existing_price_workflow(self):
        candidate=market(); calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def run(cmd, **kwargs):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd,0)
            with patch.object(demo,'__file__',str(root/'student2'/'verification'/'run_market_price_demo.py')), \
                 patch.object(demo.sys,'argv',['run_market_price_demo.py']), \
                 patch.object(discovery,'discover',return_value=(candidate,{'search_exhaustive':False})), \
                 patch.object(live,'observe_market',return_value={'observation_status':'book_observed','stage':'forecast_compatibility'}) as observe, \
                 patch.object(binance_raw_context,'assemble',return_value={'source':'binance:BTC/USDT'}), \
                 patch.object(enriched,'prepare',return_value={'evidence':[]}), \
                 patch.object(evidence,'build_prompt',return_value='Synthetic offline prompt'), \
                 patch.object(demo.subprocess,'run',side_effect=run):
                self.assertEqual(demo.main(),0)
            observe.assert_called_once_with(targeted_market=candidate)
            self.assertEqual(len(calls),1)
            self.assertIn('collect_binance_context.ps1',' '.join(calls[0]))
            self.assertEqual(len(list(root.rglob('targeted_discovery.json'))),1)
            self.assertEqual(len(list(root.rglob('observation.json'))),1)
            self.assertEqual(len(list(root.rglob('prompt.txt'))),1)
            self.assertFalse(list(root.rglob('result.json')))

    def test_failed_book_stops_before_binance_or_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(demo,'__file__',str(root/'student2'/'verification'/'run_market_price_demo.py')), \
                 patch.object(demo.sys,'argv',['run_market_price_demo.py']), \
                 patch.object(discovery,'discover',return_value=(market(),{'search_exhaustive':False})), \
                 patch.object(live,'observe_market',return_value={'observation_status':'failed','stage':'book_stream'}), \
                 patch.object(demo.subprocess,'run') as run:
                self.assertEqual(demo.main(),1)
                run.assert_not_called()

    def test_live_demo_uses_actual_adapter_and_one_mocked_model_call(self):
        from tests.test_market_price_context import PRED as PRICE_PRED, context, observation as price_observation
        from tests.test_market_evidence_specialist import response
        calls=[]
        report=price_observation()
        report.update(observation_status='book_observed',stage='forecast_compatibility',
                      finished_at_utc=PRICE_PRED.isoformat())
        class Clock(demo.datetime):
            @classmethod
            def now(cls,tz=None):return PRICE_PRED
        def model_call(prompt):
            calls.append(prompt)
            return response()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(demo,'__file__',str(root/'student2'/'verification'/'run_market_price_demo.py')), \
                 patch.object(demo.sys,'argv',['run_market_price_demo.py','--live','--model','mock-model']), \
                 patch.dict(demo.os.environ,{'GOOGLE_API_KEY':'offline-test-key'}), \
                 patch.object(demo,'datetime',Clock), \
                 patch.object(discovery,'discover',return_value=(market(),{'search_exhaustive':False})), \
                 patch.object(live,'observe_market',return_value=report), \
                 patch.object(binance_raw_context,'assemble',return_value=context()), \
                 patch.object(evidence,'gemini_call',return_value=model_call), \
                 patch.object(demo.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
                self.assertEqual(demo.main(),0)
            results=list(root.rglob('result.json'))
            self.assertEqual(len(results),1)
            result=json.loads(results[0].read_text(encoding='utf-8'))
            self.assertEqual(result['agent'],'Betting Odds Specialist')
            self.assertAlmostEqual(result['p_up'],.52)
            self.assertIn('Assumptions:',result['evidence'][0])
            metadata=json.loads((results[0].parent/'metadata.json').read_text(encoding='utf-8'))
            self.assertFalse(metadata['team_settlement_contract_established'])
            self.assertEqual(metadata['prediction_time'],PRICE_PRED.isoformat())
            self.assertEqual(len(calls),1)

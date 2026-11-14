"""Offline tests: fabricated books and mocked transport, never live forecasts."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from student2.agents import limitless_live as live
from student2.agents import betting_odds_specialist as odds

NOW = datetime(2026, 11, 13, 16, 59, 45, tzinfo=timezone.utc)


def market(tie='down'):
    first, last = "Nov 13 '26 12:00", "Nov 14 '26 12:00"
    rule = ('This market will resolve to "Up" if the "Close" price for the Binance 1 minute candle for BTC/USDT '
            + first + ' in the ET timezone (noon) is lower than the final "Close" price for the ' + last + ' ET candle. '
            'This market will resolve to "Down" if the "Close" price for the Binance 1 minute candle for BTC/USDT '
            + first + ' in the ET timezone (noon) is higher than the final "Close" price for the ' + last + ' ET candle. '
            'If the final "Close" price for both of these candles is exactly equal on Binance, this market will resolve '
            + tie + '. The resolution source for this market is Binance, specifically the BTC/USDT "Close" prices '
            'with 1m and Candles selected.')
    return dict(id=1, slug='synthetic-btc-daily', synthetic=True, title='SYNTHETIC BTC Daily',
                description=rule, startAt='2026-11-13T17:00:00Z', expirationTimestamp=1794675600000,
                prices=[.01,.99], tokens={'yes':'YES_TOKEN','no':'NO_TOKEN'},
                tradeType='clob', marketType='single', automationType='lumy', expired=False,
                status='FUNDED', liquidityFormatted='1000')


def frame():
    return dict(marketSlug='synthetic-btc-daily', timestamp=NOW.isoformat(), version=2,
                orderbook={'tokenId':'YES_TOKEN','bids':[{'price':.6,'size':2000000}],
                           'asks':[{'price':.66,'size':3000000}]})


def contract():
    return dict(prediction_time='2026-11-13T17:00:00Z',target_time='2026-11-14T17:00:00Z',
                price_source='binance:BTC/USDT',reference_price_definition='1m_candle_close',tie_definition='down')


class LiveReaderTests(unittest.TestCase):
    def test_prices_and_depth_come_from_book(self):
        q=live.read_book_event(frame(),market(),NOW)
        self.assertAlmostEqual(q['yes_payout_midpoint'],.63)
        self.assertEqual(q['displayed_bid_shares'],2)
        self.assertEqual(q['displayed_ask_shares'],3)

    def test_invalid_book_variants(self):
        changes = [lambda f: f.update(marketSlug='other'),
                   lambda f: f.update(timestamp=(NOW-timedelta(seconds=31)).isoformat()),
                   lambda f: f.update(timestamp=(NOW+timedelta(seconds=1)).isoformat()),
                   lambda f: f.update(version=0), lambda f: f.update(version=True),
                   lambda f: f['orderbook'].update(tokenId='NO_TOKEN'),
                   lambda f: f['orderbook'].update(bids=[]),
                   lambda f: f['orderbook']['asks'][0].update(price=.5),
                   lambda f: f['orderbook']['bids'][0].update(size=0),
                   lambda f: f['orderbook']['bids'][0].update(price=float('nan')),
                   lambda f: f['orderbook']['bids'][0].update(price=True),
                   lambda f: f['orderbook']['bids'].append({'price':.7,'size':1000000})]
        for mutate in changes:
            with self.subTest(mutate=mutate):
                f=frame(); mutate(f)
                with self.assertRaises(ValueError): live.read_book_event(f,market(),NOW)

    def test_unknown_mapping_rejected(self):
        m=market();m['tradeType']='amm'
        with self.assertRaises(ValueError):live.read_book_event(frame(),m,NOW)

    def test_initial_snapshot_never_used(self):
        t=live.BookTracker(market(),NOW-timedelta(seconds=1))
        self.assertIsNone(t.accept(frame(),NOW))
        self.assertIsNone(t.accept(frame(),NOW))

    def test_later_version_accepted(self):
        t=live.BookTracker(market(),NOW-timedelta(seconds=1))
        f=frame();f['version']=1
        t.accept(f,NOW)
        self.assertAlmostEqual(t.accept(frame(),NOW)['yes_payout_midpoint'],.63)

    def test_version_reversal_rejected(self):
        t=live.BookTracker(market(),NOW)
        t.accept(frame(),NOW); f=frame();f['version']=1
        with self.assertRaises(ValueError):t.accept(f,NOW)

    def test_timestamp_reversal_rejected(self):
        t=live.BookTracker(market(),NOW)
        t.accept(frame(),NOW);f=frame();f['version']=3;f['timestamp']=(NOW-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):t.accept(f,NOW)

    def test_events_before_connection_not_used(self):
        t=live.BookTracker(market(),NOW+timedelta(seconds=2))
        t.accept(frame(),NOW);f=frame();f['version']=3
        self.assertIsNone(t.accept(f,NOW))

    def test_same_frame_binding_not_rest_price(self):
        m=market();f=frame();q=live.read_book_event(f,m,NOW)
        result=live.assess_capture(m,q,f,contract())
        self.assertEqual(result.data_status,'sufficient_data')
        self.assertAlmostEqual(result.probability_up,.63)
        self.assertEqual(m['prices'],[.01,.99])
        self.assertIn('Synthetic fixture',result.explanation)

    def test_tampered_quote_rejected(self):
        m=market();f=frame();q=live.read_book_event(f,m,NOW);q['yes_payout_midpoint']=.99
        with self.assertRaises(ValueError):live.assess_capture(m,q,f,contract())

    def test_split_payout_not_strict_up_probability(self):
        m=market('50-50');f=frame();q=live.read_book_event(f,m,NOW)
        result=live.assess_capture(m,q,f,contract())
        self.assertIsNone(result.probability_up)
        self.assertIn('tie',result.explanation)

    def test_started_market_not_rolling_24h(self):
        m=market();f=frame();later=NOW+timedelta(minutes=1);f['timestamp']=later.isoformat()
        q=live.read_book_event(f,m,later)
        self.assertIsNone(live.assess_capture(m,q,f,contract()).probability_up)

    def test_daily_discovery_not_five_minute_substitution(self):
        m=market('50-50')
        self.assertEqual(live.choose_daily([m],NOW)[3],'50-50')
        with self.assertRaises(ValueError):live.choose_daily([m],NOW,slug='wrong')
        m['description']=m['description'].replace("Nov 14 '26 12:00","Nov 13 '26 12:05")
        with self.assertRaises(ValueError):live.choose_daily([m],NOW)

    def test_ended_market_not_selected(self):
        with self.assertRaises(ValueError):live.choose_daily([market()],NOW+timedelta(days=2))

    def test_malformed_verified_times_returns_missing(self):
        self.assertIsNone(odds.run_betting_odds_specialist(**contract(),markets=[market()],now=NOW,
                                                          verified_quote_times=[]).probability_up)

    def test_bad_numeric_options_fail_closed(self):
        for value in ('bad', True, float('nan')):
            result=odds.run_betting_odds_specialist(**contract(),markets=[market()],now=NOW,max_quote_age_seconds=value)
            self.assertIsNone(result.probability_up)

    def test_invalid_reported_liquidity_rejected(self):
        m=market();m['liquidityFormatted']='bad';f=frame();q=live.read_book_event(f,m,NOW)
        self.assertIsNone(live.assess_capture(m,q,f,contract()).probability_up)

    def test_missing_liquidity_not_invented_from_volume(self):
        m=market();m.pop('liquidityFormatted');m['volumeFormatted']='1000000'
        q=live.read_book_event(frame(),m,NOW)
        result=live.assess_capture(m,q,frame(),contract())
        self.assertIn('volume is not liquidity',' '.join(result.limitations))

    def test_bad_timeout_option_returns_missing_without_network(self):
        for value in ('bad', True, None):
            called=[]
            result=odds.run_betting_odds_specialist(**contract(),now=NOW,
                timeout_seconds=value,fetch_page=lambda *a: called.append(a))
            self.assertIsNone(result.probability_up)
            self.assertEqual(called,[])

    def test_network_error_is_sanitized(self):
        def fail(*args):raise OSError('secret')
        result=odds.run_betting_odds_specialist(**contract(),now=NOW,fetch_page=fail)
        self.assertNotIn('secret',result.explanation)

    def test_provider_value_error_is_sanitized(self):
        def fail(*args):raise ValueError('raw provider response must not be exposed')
        result=odds.run_betting_odds_specialist(**contract(),now=NOW,fetch_page=fail)
        self.assertIsNone(result.probability_up)
        self.assertIn('ValueError',result.explanation)
        self.assertNotIn('raw provider response',result.explanation)

    def test_transport_uses_public_subscription_and_closes(self):
        records={}
        class FakeClient:
            def __init__(self,**kwargs):self.handlers={};records['options']=kwargs
            def on(self,name,handler,namespace):self.handlers[name]=handler
            def emit(self,name,payload,namespace):records['emit']=(name,payload,namespace)
            def connect(self,url,**kwargs):
                records['connect']=(url,kwargs);self.handlers['connect']()
                for v in (1,2):
                    f=frame();f['version']=v;f['timestamp']=datetime.now(timezone.utc).isoformat()
                    self.handlers['orderbookUpdate'](f)
            def disconnect(self):records['closed']=True
        q,_=live.capture_book(market(),timeout_seconds=1,client_factory=FakeClient)
        self.assertAlmostEqual(q['yes_payout_midpoint'],.63)
        self.assertFalse(records['options']['reconnection'])
        self.assertEqual(records['emit'][0],'subscribe_market_prices')
        self.assertEqual(records['connect'][1]['transports'],['websocket'])
        self.assertTrue(records['closed'])

    def test_transport_disconnect_does_not_fabricate_quote(self):
        records={}
        class FakeClient:
            def __init__(self,**kwargs):self.handlers={}
            def on(self,name,handler,namespace):self.handlers[name]=handler
            def connect(self,*args,**kwargs):self.handlers['disconnect']()
            def disconnect(self):records['closed']=True
        with self.assertRaises(RuntimeError):live.capture_book(market(),timeout_seconds=1,client_factory=FakeClient)
        self.assertTrue(records['closed'])

    def test_observation_does_not_silently_become_team_forecast(self):
        m=market('50-50');f=frame();q=live.read_book_event(f,m,NOW)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return NOW
        with patch.object(live,'datetime',Clock):
            report=live.observe_market(fetch_page=lambda *a:{'data':[m],'totalMarketsCount':1},capture=lambda m:(q,f))
        self.assertEqual(report['observation_status'],'book_observed')
        self.assertIsNone(report['team_probability_up'])
        self.assertEqual(report['market_tie_rule'],'50-50')
        self.assertAlmostEqual(report['quote']['yes_payout_midpoint'],.63)

    def test_failed_observation_retains_no_probability(self):
        def failed(*args):raise OSError('secret')
        report=live.observe_market(fetch_page=failed)
        self.assertEqual(report['observation_status'],'failed')
        self.assertIsNone(report['team_probability_up'])
        self.assertNotIn('secret',str(report))

    def test_http_failure_reports_safe_page_and_status_without_sensitive_details(self):
        calls=[]
        def fetch(page,limit,timeout):
            calls.append((page,limit,timeout))
            if page == 1:
                return {'data':[{'id':n} for n in range(1,26)],'totalMarketsCount':26}
            raise HTTPError('https://secret.example/secret-query',403,'secret-reason',
                            {'Authorization':'secret-header'},io.BytesIO(b'secret-body'))
        def capture(*args):raise AssertionError('No book capture after listing failure')
        report=live.observe_market(fetch_page=fetch,capture=capture)
        self.assertEqual(report['stage'],'market_listing')
        self.assertEqual(report['error_type'],'HTTPError')
        self.assertEqual(report['http_status'],403)
        self.assertEqual(report['http_page'],2)
        self.assertEqual(report['endpoint_path'],'/markets/active')
        self.assertEqual([call[0] for call in calls],[1,2])
        with tempfile.TemporaryDirectory() as tmp:
            output=io.StringIO()
            with patch.object(live,'observe_market',return_value=report), \
                    patch.object(sys,'argv',['limitless_live','--out',tmp]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(live.main(),1)
            self.assertIn('HTTP status: 403 | Page: 2 | Endpoint: /markets/active',output.getvalue())
            saved=json.loads(next(Path(tmp).rglob('observation.json')).read_text(encoding='utf-8'))
        for value in ('secret-query','secret-reason','secret-header','secret-body'):
            self.assertNotIn(value,str(report))
            self.assertNotIn(value,output.getvalue())
            self.assertNotIn(value,str(saved))


if __name__=='__main__':unittest.main()

"""Offline tests of saved listing discovery; stream events are synthetic."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.test_limitless_live_reader import NOW, frame, market
from market_data.prediction_markets import limitless_live as live


def listing():
    return {'source': 'https://api.limitless.exchange/markets/active',
            'started_at_utc': (NOW-timedelta(seconds=3)).isoformat(),
            'finished_at_utc': NOW.isoformat(),
            'pages': [{'page': 1, 'retrieved_at_utc': (NOW-timedelta(seconds=2)).isoformat(),
                       'response': {'data': [market()], 'totalMarketsCount': 1}}]}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class SavedListingTests(unittest.TestCase):
    def test_complete_two_page_listing_uses_all_pages(self):
        data=listing()
        data['pages'][0]['response']={'data':[{'id':i} for i in range(2,27)], 'totalMarketsCount':26}
        data['pages'].append({'page':2,'retrieved_at_utc':(NOW-timedelta(seconds=1)).isoformat(),
                              'response':{'data':[market()],'totalMarketsCount':26}})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'listing.json'; path.write_text(json.dumps(data),encoding='utf-8')
            markets,metadata=live._load_saved_listing(path)
        self.assertEqual(len(markets),26)
        self.assertEqual(markets[-1]['slug'],market()['slug'])
        self.assertEqual(metadata['listing_page_count'],2)

    def test_saved_listing_selects_without_listing_network(self):
        m=market(); f=frame(); q=live.read_book_event(f,m,NOW); calls=[]
        def unexpected_network(*args):
            raise AssertionError('Saved listing must not fetch REST pages')
        def capture(selected):
            calls.append(selected['slug']); return q,f
        data=listing()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'listing.json'; path.write_text(json.dumps(data),encoding='utf-8')
            with patch.object(live,'datetime',Clock):
                report=live.observe_market(listing_file=path,fetch_page=unexpected_network,capture=capture)
        self.assertEqual(report['observation_status'],'book_observed')
        self.assertEqual(report['listing_source'],'saved_powershell_listing')
        self.assertEqual(calls,[m['slug']])
        self.assertEqual(report['quote']['provider_event_time'],NOW.isoformat())
        self.assertNotEqual(report['quote']['provider_event_time'],data['pages'][0]['retrieved_at_utc'])
        self.assertIsNone(report['team_probability_up'])

    def test_listing_time_cannot_replace_missing_or_stale_stream_time(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return NOW
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'listing.json'; path.write_text(json.dumps(listing()),encoding='utf-8')
            for stamp in (None,(NOW-timedelta(seconds=31)).isoformat()):
                with self.subTest(stamp=stamp):
                    f=frame(); f['timestamp']=stamp
                    def capture(selected): return {'received_at':NOW.isoformat()},f
                    with patch.object(live,'datetime',Clock):
                        report=live.observe_market(listing_file=path,capture=capture)
                    self.assertEqual(report['observation_status'],'failed')
                    self.assertEqual(report['stage'],'book_stream')
                    self.assertIsNone(report['team_probability_up'])

    def test_incomplete_inconsistent_and_duplicate_pages_fail_closed(self):
        base=listing(); base['pages'][0]['response']['totalMarketsCount']=2
        duplicate=copy.deepcopy(base)
        duplicate['pages'].append({'page':2,'retrieved_at_utc':(NOW-timedelta(seconds=1)).isoformat(),
                                   'response':{'data':[market()],'totalMarketsCount':2}})
        changed=copy.deepcopy(duplicate)
        changed['pages'][1]['response']['data'][0]['id']=2
        changed['pages'][1]['response']['totalMarketsCount']=3
        wrong_page=copy.deepcopy(duplicate); wrong_page['pages'][1]['page']=3
        def no_capture(*args):raise AssertionError('Invalid listing must not reach stream')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'listing.json'
            for data in (base,duplicate,changed,wrong_page):
                with self.subTest(data=data):
                    path.write_text(json.dumps(data),encoding='utf-8')
                    report=live.observe_market(listing_file=path,capture=no_capture)
                    self.assertEqual(report['observation_status'],'failed')
                    self.assertEqual(report['stage'],'market_listing')
                    self.assertIsNone(report['team_probability_up'])

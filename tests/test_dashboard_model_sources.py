"""Offline checks for the existing dashboard collector and model adapters."""
import json
from datetime import timedelta
from types import SimpleNamespace

from dashboard.service import forecast_snapshot
from market_data.prediction_markets.dashboard_collect import collect_betting_observation
from tests.test_market_evidence_specialist import PRED, TARGET, observation, response


def test_live_book_capture_is_supplied_as_evidence_to_dashboard_adapters():
    capture = observation()
    market = capture['market']
    calls = []
    def discovery():
        calls.append('discover')
        return market, {'search_exhaustive': False, 'max_pages': 3,
                        'detail_retrieved_at_utc': PRED.isoformat(), 'limitations': []}
    def observer(*, targeted_market):
        assert targeted_market is market
        calls.append('capture')
        return capture
    data, status = collect_betting_observation(discovery=discovery, observer=observer)
    assert status['status'] == 'collected'
    assert data['student2_markets_observed_at'] == capture['quote']['received_at']
    assert calls == ['discover', 'capture']  # No model call during collection.

    data.update({
        'student2_news_articles': [{'evidence_id': 'news-1', 'title': 'Bitcoin demand rises',
            'publisher': 'Example', 'publication_timestamp': (PRED-timedelta(hours=1)).isoformat(),
            'url': 'https://example.com/article', 'summary': 'Bitcoin market demand increased.'}],
        'student2_news_observed_at': PRED.isoformat(),
        'student2_mood_readings': [{'evidence_id': 'mood-1',
            'timestamp': (PRED-timedelta(hours=1)).isoformat(),
            'timestamp_unix': int((PRED-timedelta(hours=1)).timestamp()),
            'score': 55, 'classification': 'Greed', 'source': 'Offline test'}],
        'student2_mood_observed_at': PRED.isoformat(),
    })
    class MockGemini:
        usage = []
        def __call__(self, prompt):
            calls.append('betting')
            return json.dumps(response())
        def invoke(self, messages, **kwargs):
            prompt = messages[0].content
            kind = 'mood' if 'fng_readings:' in prompt else 'news'
            calls.append(kind)
            return SimpleNamespace(content=json.dumps({
                'agent': 'Mood Specialist' if kind == 'mood' else 'News Specialist',
                'prediction_time': PRED.isoformat(), 'target_time': TARGET.isoformat(),
                'probability_up': .52, 'data_status': 'sufficient_data',
                'explanation': 'Offline mock response',
                'evidence_ids': ['mood-1' if kind == 'mood' else 'news-1'],
                'limitations': [],
            }))
    quote = {'price': 70000, 'bar_close_time': (PRED-timedelta(seconds=1)).isoformat(),
             'retrieved_at': PRED.isoformat(), 'source': 'synthetic offline test'}
    result = forecast_snapshot(data, quote, client=MockGemini(), decision_time=PRED)
    results = {item['role']: item for item in result['agents']}
    for role in ('News', 'Mood', 'Betting odds'):
        assert results[role]['audit']['passed'], results[role]
    assert calls == ['discover', 'capture', 'news', 'mood', 'betting']


def test_failed_market_capture_does_not_supply_market_or_call_model():
    data, status = collect_betting_observation(
        discovery=lambda: ({}, {}),
        observer=lambda **kwargs: {'observation_status': 'failed', 'stage': 'book_stream',
                                   'error': 'No usable live observation'})
    assert not data
    assert status['status'] == 'unavailable'
    assert 'book_stream' in status['reason']

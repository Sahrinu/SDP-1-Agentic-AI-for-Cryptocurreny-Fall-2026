"""Collect a live market-book observation for the dashboard; no model calls."""
from datetime import datetime

from market_data.prediction_markets.limitless_live import observe_market
from market_data.prediction_markets.limitless_targeted_discovery import discover


def collect_betting_observation(*, discovery=None, observer=None):
    """Return inputs only after bounded discovery and fresh book capture.

    The streamed book is evidence for the existing model adapter, never itself a
    forecast. The adapter revalidates it against the actual decision timestamp.
    """
    discovery = discovery or discover
    observer = observer or observe_market
    try:
        market, provenance = discovery()
        observation = observer(targeted_market=market)
        if observation.get('observation_status') != 'book_observed':
            stage = observation.get('stage', 'book capture')
            reason = observation.get('error', 'No usable live market-book observation')
            return {}, {'status': 'unavailable', 'reason': f'{stage}: {reason}'}
        received = observation['quote']['received_at']
        timestamp = datetime.fromisoformat(received.replace('Z', '+00:00'))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError('Book receipt time must be timezone-aware')
        observation['discovery'] = {
            'search_exhaustive': provenance.get('search_exhaustive'),
            'max_pages': provenance.get('max_pages'),
            'detail_retrieved_at_utc': provenance.get('detail_retrieved_at_utc'),
            'limitations': provenance.get('limitations', []),
        }
        return {'student2_market_observation': observation,
                'student2_markets_observed_at': received}, {
                    'status': 'collected', 'received_at': received,
                    'reason': 'Live book captured; the adapter revalidates freshness at forecast time',
                }
    except Exception as exc:
        # Preserve useful error class without leaking provider bodies or URLs.
        return {}, {'status': 'unavailable',
                    'reason': f'Bounded market discovery or capture failed: {type(exc).__name__}'}

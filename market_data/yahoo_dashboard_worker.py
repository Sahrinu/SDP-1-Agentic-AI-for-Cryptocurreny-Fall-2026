"""Isolated Yahoo dashboard collection; parent enforces a hard deadline.

At most two target requests are sent to each URL in one worker. Cookie/crumb
negotiation is separate but is covered by the parent's wall-clock timeout.
This process never reads model credentials or calls a model.
"""
from __future__ import annotations

import json
import sys
from functools import wraps
from pathlib import Path


class YahooRequestLimit(RuntimeError):
    """The target endpoint has exhausted its bounded request budget."""


class YahooClientError(RuntimeError):
    """A non-rate-limit client error is not retried."""


def capped_sender(send, counts, limit=2):
    """Count actual target sends across yfinance retries and cookie fallback."""
    @wraps(send)
    def limited(*args, **kwargs):
        url = kwargs.get('url') if 'url' in kwargs else (args[0] if args else None)
        if not isinstance(url, str):
            raise ValueError('Yahoo request URL is unavailable')
        if counts.get(url, 0) >= limit:
            raise YahooRequestLimit('Yahoo request budget exhausted')
        counts[url] = counts.get(url, 0) + 1
        response = send(*args, **kwargs)
        status = response.status_code
        if 400 <= status < 500 and status != 429:
            raise YahooClientError('Yahoo request rejected')
        return response
    return limited


def configure_yfinance():
    """Bound target requests even if the installed client changes strategies."""
    import yfinance as yf
    from yfinance.data import YfData

    yf.config.network.retries = 1
    counts = {}
    original = YfData._make_request

    @wraps(original)
    def bounded(self, url, request_method, *args, **kwargs):
        return original(self, url, capped_sender(request_method, counts), *args, **kwargs)

    YfData._make_request = bounded


def main():
    kind, folder = sys.argv[1], Path(sys.argv[2])
    configure_yfinance()
    if kind == 'news':
        from agents.specialists import news_specialist as news
        retry = news.yf_retry
        news.yf_retry = lambda func: retry(func, max_retries=1, base_delay=1.0)
        result = news._fetch_recent_btc_news()
        (folder / 'result.json').write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
    elif kind == 'macro':
        from datetime import datetime, timedelta, timezone
        from market_data.macro import collect
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        collect(end-timedelta(days=60), end, output=folder/'macro.csv')
    else:
        raise ValueError('Unsupported Yahoo collection')


if __name__ == '__main__':
    main()

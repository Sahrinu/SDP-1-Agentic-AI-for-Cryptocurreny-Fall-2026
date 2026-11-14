from datetime import datetime, timezone

import pytest
from yfinance.exceptions import YFRateLimitError

from student2.compat import yahoo_compat
from student2.compat.errors import VendorUnavailableError


def test_extracts_nested_and_flat_yahoo_articles():
    nested = yahoo_compat._extract_article_data({"content": {
        "title": "Bitcoin rises", "summary": "Fresh demand", "provider": {"displayName": "Wire"},
        "canonicalUrl": {"url": "https://example.com/news"}, "pubDate": "2025-01-01T00:00:00Z",
    }})
    assert nested == {
        "title": "Bitcoin rises", "summary": "Fresh demand", "publisher": "Wire",
        "link": "https://example.com/news", "pub_date": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    flat = yahoo_compat._extract_article_data({"title": "Flat", "providerPublishTime": 1735689600})
    assert flat["pub_date"] == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert flat["publisher"] == "Unknown"


def test_retry_reports_failure_and_retries_rate_limit(monkeypatch):
    calls = []
    delays = []
    monkeypatch.setattr(yahoo_compat.time, "sleep", delays.append)

    def request():
        calls.append(None)
        raise YFRateLimitError()

    with pytest.raises(VendorUnavailableError, match="rate limited after 2 retries"):
        yahoo_compat.yf_retry(request, max_retries=2, base_delay=0.5)
    assert len(calls) == 3
    assert delays == [0.5, 1.0]

    def fail():
        raise RuntimeError("offline failure")

    with pytest.raises(VendorUnavailableError, match="request failed: RuntimeError"):
        yahoo_compat.yf_retry(fail)

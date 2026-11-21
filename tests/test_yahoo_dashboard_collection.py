"""Offline bounds for real dashboard Yahoo collectors; no provider requests."""
import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from market_data.yahoo_dashboard import (YahooCollectionFailed,
    YahooCollectionTimedOut, collect_yahoo)
from market_data.yahoo_dashboard_worker import (YahooClientError,
    YahooRequestLimit, capped_sender)


def test_target_send_budget_includes_all_client_retries():
    calls = []
    def send(**kwargs):
        calls.append(kwargs['url'])
        return SimpleNamespace(status_code=503)
    counts = {}
    limited = capped_sender(send, counts)
    limited(url='https://example.invalid/news')
    limited(url='https://example.invalid/news')
    with pytest.raises(YahooRequestLimit):
        limited(url='https://example.invalid/news')
    assert len(calls) == 2
    assert counts == {'https://example.invalid/news': 2}


def test_non_rate_limit_client_error_is_not_retried():
    calls = []
    def send(**kwargs):
        calls.append(kwargs['url'])
        return SimpleNamespace(status_code=403)
    with pytest.raises(YahooClientError):
        capped_sender(send, {})(url='https://example.invalid/news')
    assert len(calls) == 1


def test_worker_timeout_fails_closed_without_persisting_partial_data(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        assert kwargs['timeout'] == 45
        raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(YahooCollectionTimedOut):
        collect_yahoo('news', tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_successful_news_returns_worker_output_without_a_second_attempt(tmp_path, monkeypatch):
    calls = []
    expected = [{'title': 'A real-shaped offline fixture', 'publication_timestamp': '2026-11-16T00:00:00Z'}]
    def run(cmd, **kwargs):
        calls.append(cmd)
        assert 'GOOGLE_API_KEY' not in kwargs['env']
        assert 'COINGECKO_DEMO_API_KEY' not in kwargs['env']
        from pathlib import Path
        (Path(cmd[-1])/'result.json').write_text(json.dumps(expected), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setenv('GOOGLE_API_KEY', 'fake-test-only-key')
    monkeypatch.setenv('COINGECKO_DEMO_API_KEY', 'fake-test-only-key')
    monkeypatch.setattr(subprocess, 'run', run)
    assert collect_yahoo('news', tmp_path) == expected
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_failed_worker_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(YahooCollectionFailed):
        collect_yahoo('macro', tmp_path)
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_macro_success_retains_retrieval_provenance(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        from pathlib import Path
        folder = Path(cmd[-1])
        (folder/'macro.csv').write_text('date,dxy,retrieval_completed_at\n2026-11-15,100,2026-11-16T10:00:00+00:00\n')
        (folder/'macro_raw').mkdir()
        (folder/'macro_raw'/'observation.json').write_text('{"publication_basis":"not_recorded"}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    frame = collect_yahoo('macro', tmp_path)
    assert frame.index[0] == pd.Timestamp('2026-11-15', tz='UTC')
    assert frame.iloc[0].retrieval_completed_at == '2026-11-16T10:00:00+00:00'
    assert (tmp_path/'macro_raw'/'observation.json').is_file()


def test_snapshot_collects_news_and_macro_and_defers_betting(tmp_path, monkeypatch):
    from market_data import live, yahoo_dashboard

    receipt = '2026-11-16T10:00:00+00:00'
    calls = []
    def yahoo(kind, folder):
        calls.append(kind)
        if kind == 'news':
            return [{'title': 'Offline fixture', 'publication_timestamp': receipt}]
        return pd.DataFrame({'dxy': [100], 'treasury_10y': [4],
                             'retrieval_completed_at': [receipt]},
                            index=pd.DatetimeIndex(['2026-11-15'], tz='UTC', name='date'))
    def blocked(*args, **kwargs):
        raise RuntimeError('offline test must not access providers')
    monkeypatch.setattr(yahoo_dashboard, 'collect_yahoo', yahoo)
    monkeypatch.setattr(live, 'collect_bitcoin', blocked)
    monkeypatch.setattr(live, 'collect_chain', blocked)
    monkeypatch.setattr(live, 'get_json', blocked)
    monkeypatch.setattr(live, 'collect_betting_inputs', blocked)
    monkeypatch.delenv('COINGECKO_DEMO_API_KEY', raising=False)
    data, statuses = live.collect_snapshot(tmp_path)
    assert calls == ['macro', 'news']
    assert statuses['macro']['status'] == statuses['news']['status'] == 'collected'
    assert statuses['betting_odds']['status'] == 'unavailable'
    assert data['student2_news_articles'][0]['title'] == 'Offline fixture'
    assert 'student2_market_observation' not in data
    assert set(data['macro']['dxy_available_at']) == {receipt}

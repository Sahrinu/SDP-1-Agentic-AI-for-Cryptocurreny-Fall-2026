"""Bounded, read-only Yahoo collection for the dashboard.

Each source gets an isolated worker and a 45-second wall-clock deadline. The
worker caps target endpoint sends at two per URL, including yfinance retries;
cookie/crumb requests are also subject to the wall-clock deadline. A timed-out
worker is killed and reaped by subprocess.run before the collector continues.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

YAHOO_TIMEOUT_SECONDS = 45


class YahooCollectionFailed(RuntimeError):
    pass


class YahooCollectionTimedOut(TimeoutError):
    pass


def _public_worker_environment():
    """Do not propagate credentials to a public, unauthenticated worker."""
    sensitive_parts = ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')
    sensitive_prefixes = ('AWS_', 'AZURE_', 'GOOGLE_', 'GEMINI_', 'OPENAI_',
                          'ANTHROPIC_', 'COINGECKO_')
    return {key: value for key, value in os.environ.items()
            if not any(part in key.upper() for part in sensitive_parts)
            and not key.upper().startswith(sensitive_prefixes)}


def collect_yahoo(kind, directory, *, timeout=YAHOO_TIMEOUT_SECONDS):
    """Run one source without leaking subprocess output or partial snapshots."""
    if kind not in ('news', 'macro'):
        raise ValueError('Unsupported Yahoo collection')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='yahoo-', dir=directory) as temp:
        folder = Path(temp)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        try:
            result = subprocess.run([sys.executable, '-m', 'market_data.yahoo_dashboard_worker',
                                     kind, str(folder)], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout,
                                    check=False, creationflags=flags,
                                    env=_public_worker_environment())
        except subprocess.TimeoutExpired as exc:
            raise YahooCollectionTimedOut('Yahoo collection timed out') from exc
        if result.returncode != 0:
            raise YahooCollectionFailed('Yahoo collection failed')
        if kind == 'news':
            records = json.loads((folder/'result.json').read_text(encoding='utf-8'))
            if not isinstance(records, list):
                raise YahooCollectionFailed('Yahoo news response is not a list')
            return records
        frame = pd.read_csv(folder/'macro.csv', index_col='date', parse_dates=['date'])
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame.index.name = 'date'
        shutil.copy2(folder/'macro.csv', directory/'macro.csv')
        shutil.copytree(folder/'macro_raw', directory/'macro_raw')
        return frame

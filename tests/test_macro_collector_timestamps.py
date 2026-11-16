"""Offline checks for truthful Yahoo collector timing and provenance."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from market_data.macro import collect


START = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def test_completion_is_measured_after_both_downloads(tmp_path):
    events = []
    readings = iter((START, START + timedelta(seconds=9)))

    def clock():
        value = next(readings)
        events.append(('clock', value))
        return value

    def downloader(symbol, **kwargs):
        events.append(('download', symbol))
        assert kwargs['interval'] == '1d'
        return pd.DataFrame({'Close': [100.0, 101.0]},
                            index=pd.to_datetime(['2026-11-12', '2026-11-13']))

    # now is a validation reference only; it must not replace real receipt time.
    frame = collect('2026-11-12', '2026-11-15', output=tmp_path/'macro.csv',
                    downloader=downloader, now=START-timedelta(hours=1), clock=clock)
    assert [e[0] for e in events] == ['clock', 'download', 'download', 'clock']
    archive = json.loads(next((tmp_path/'macro_raw').glob('*.json')).read_text(encoding='utf-8'))
    assert archive['collection_started_at'] == START.isoformat()
    assert archive['retrieval_completed_at'] == (START+timedelta(seconds=9)).isoformat()
    assert archive['retrieved_at'] == archive['retrieval_completed_at']
    assert archive['publication_basis'] == 'not_recorded'
    assert archive['availability_basis'] == 'assumed_next_UTC_midnight_not_verified_vintage'
    assert set(frame['retrieval_completed_at']) == {archive['retrieval_completed_at']}
    assert set(frame['publication_basis']) == {'not_recorded'}
    assert frame.iloc[-1]['dxy_observed_on'] == '2026-11-13'
    assert frame.iloc[-1]['dxy_available_at'] == '2026-11-14T00:00:00Z'
    assert bool(frame.iloc[-1]['dxy_forward_filled'])
    saved = pd.read_csv(tmp_path/'macro.csv')
    assert saved.iloc[-1]['dxy_observed_on'] == '2026-11-13'
    assert saved.iloc[-1]['retrieval_completed_at'] == archive['retrieval_completed_at']


def test_future_bar_is_not_used_for_prior_days_and_stale_value_is_missing(tmp_path):
    def downloader(symbol, **kwargs):
        return pd.DataFrame({'Close': [100.0, 999.0]},
                            index=pd.to_datetime(['2026-11-06', '2026-11-16']))
    frame = collect('2026-11-06', '2026-11-15', output=tmp_path/'macro.csv',
                    downloader=downloader, clock=lambda: START)
    assert frame.loc['2026-11-10', 'dxy'] == 100.0
    assert pd.isna(frame.loc['2026-11-11', 'dxy'])
    assert not (frame['dxy'] == 999.0).any()
    assert frame.loc['2026-11-15', 'dxy_observed_on'] == '2026-11-06'


@pytest.mark.parametrize('times', [
    (datetime(2026, 11, 16, 10), START),
    (START, datetime(2026, 11, 16, 10)),
    (START, START-timedelta(seconds=1)),
])
def test_invalid_or_backwards_clock_fails_before_writing(tmp_path, times):
    readings = iter(times)
    def downloader(symbol, **kwargs):
        return pd.DataFrame({'Close': [100.0]}, index=pd.to_datetime(['2026-11-13']))
    with pytest.raises(ValueError):
        collect('2026-11-13', '2026-11-13', output=tmp_path/'macro.csv',
                downloader=downloader, clock=lambda: next(readings))
    assert not list(tmp_path.iterdir())

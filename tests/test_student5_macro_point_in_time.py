"""Offline, synthetic point-in-time macro-availability tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from market_data.macro_point_in_time import MacroPolicy, select_macro_asof


SATURDAY = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
SUNDAY = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def friday(**overrides):
    return {
        'series': 'usd_index',
        'value': 101.25,
        'observation_date': '2026-11-13',
        'published_at': '2026-11-13T14:00:00Z',
        'available_at': '2026-11-13T15:00:00Z',
        'source': 'SYNTHETIC_TEST_SOURCE',
        **overrides,
    }


def policy(days=2, hours=48, zone='UTC'):
    return MacroPolicy(max_observation_age_days=days,
                       max_availability_age_hours=hours,
                       calendar_timezone=zone)


def selected(records, prediction=SATURDAY, *, configured=None):
    return select_macro_asof(records, prediction,
                             policies={'usd_index': configured or policy()})


@pytest.mark.parametrize('prediction,days,hours', [(SATURDAY, 1, 21), (SUNDAY, 2, 45)])
def test_weekend_forward_fill_preserves_source_date_and_age(prediction, days, hours):
    record = friday()
    before = deepcopy(record)
    result = selected([record], prediction)
    row = result['series']['usd_index']
    assert result['data_status'] == 'available'
    assert row['value'] == 101.25
    assert row['observation_date'] == '2026-11-13'
    assert row['published_at'] == '2026-11-13T14:00:00+00:00'
    assert row['available_at'] == '2026-11-13T15:00:00+00:00'
    assert row['observation_age_days'] == days
    assert row['availability_age_hours'] == hours
    assert row['forward_filled'] is True
    assert row['weekend_forward_filled'] is True
    assert record == before


def test_later_release_and_revision_are_not_visible_before_availability():
    original = friday()
    later = friday(value=199.0, published_at='2026-11-14T12:01:00Z',
                   available_at='2026-11-14T12:02:00Z')
    result = selected([later, original], SATURDAY)
    assert result['series']['usd_index']['value'] == original['value']
    at_release = selected([later, original], SATURDAY + timedelta(minutes=2))
    assert at_release['series']['usd_index']['value'] == later['value']
    assert at_release['series']['usd_index']['observation_date'] == '2026-11-13'


def test_unavailable_or_unpublished_input_never_forward_filled():
    future = friday(published_at='2026-11-14T12:10:00Z',
                    available_at='2026-11-14T12:11:00Z')
    row = selected([future])['series']['usd_index']
    assert row['data_status'] == 'insufficient_data'
    assert 'No observation available' in row['reason']
    assert 'value' not in row


@pytest.mark.parametrize('changes', [
    {'published_at': None},
    {'available_at': None},
    {'published_at': '2026-11-13T14:00:00'},
    {'available_at': '2026-11-13T15:00:00'},
    {'published_at': '2026-11-13T16:00:00Z'},
    {'observation_date': '2026-11-15'},
    {'observation_date': '2026-11-14'},
    {'observation_date': '13/11/2026'},
    {'value': float('nan')},
    {'value': True},
    {'source': ''},
])
def test_invalid_metadata_and_values_fail_closed(changes):
    row = selected([friday(**changes)])['series']['usd_index']
    assert row['data_status'] == 'insufficient_data'
    assert 'Invalid or conflicting' in row['reason']
    assert 'value' not in row


def test_conflicting_duplicate_vintages_are_rejected():
    result = selected([friday(), friday(value=999)])
    assert result['data_status'] == 'insufficient_data'
    assert 'conflicting' in result['series']['usd_index']['reason'].lower()


def test_latest_eligible_observation_precedes_revision_sorting():
    older = friday(observation_date='2026-11-12', value=1,
                   published_at='2026-11-14T10:00:00Z',
                   available_at='2026-11-14T10:00:00Z')
    newer = friday(value=2)
    assert selected([older, newer])['series']['usd_index']['value'] == 2


def test_observation_and_availability_staleness_are_independently_checked():
    by_observation = selected([friday()], SUNDAY, configured=policy(days=1, hours=100))
    by_availability = selected([friday()], SUNDAY, configured=policy(days=5, hours=24))
    for result in (by_observation, by_availability):
        row = result['series']['usd_index']
        assert result['data_status'] == 'insufficient_data'
        assert 'freshness' in row['reason']
        assert row['observation_date'] == '2026-11-13'
        assert row['observation_age_days'] == 2
        assert row['availability_age_hours'] == 45
        assert 'value' not in row


def test_exact_freshness_boundaries_are_allowed_but_excess_is_stale():
    boundary = policy(days=1, hours=21)
    assert selected([friday()], SATURDAY, configured=boundary)['data_status'] == 'available'
    later = selected([friday()], SATURDAY + timedelta(microseconds=1), configured=boundary)
    assert later['data_status'] == 'insufficient_data'


def test_weekend_and_observation_age_use_explicit_local_calendar():
    sunday_in_new_york = datetime(2026, 11, 16, 2, tzinfo=timezone.utc)
    local = selected([friday()], sunday_in_new_york, configured=policy(days=2, hours=100, zone='America/New_York'))
    row = local['series']['usd_index']
    assert local['data_status'] == 'available'
    assert row['observation_age_days'] == 2
    assert row['weekend_forward_filled'] is True
    utc = selected([friday()], sunday_in_new_york, configured=policy(days=2, hours=100))
    assert utc['data_status'] == 'insufficient_data'
    assert utc['series']['usd_index']['observation_age_days'] == 3


@pytest.mark.parametrize('kwargs', [
    {'max_observation_age_days': -1, 'max_availability_age_hours': 24, 'calendar_timezone': 'UTC'},
    {'max_observation_age_days': True, 'max_availability_age_hours': 24, 'calendar_timezone': 'UTC'},
    {'max_observation_age_days': 1, 'max_availability_age_hours': 0, 'calendar_timezone': 'UTC'},
    {'max_observation_age_days': 1, 'max_availability_age_hours': float('nan'), 'calendar_timezone': 'UTC'},
    {'max_observation_age_days': 1, 'max_availability_age_hours': 24, 'calendar_timezone': 'missing/zone'},
])
def test_policy_rejects_invalid_values_without_defaults(kwargs):
    with pytest.raises(ValueError):
        MacroPolicy(**kwargs)


def test_explicit_policy_and_aware_prediction_are_required():
    with pytest.raises(TypeError):
        select_macro_asof([friday()], SATURDAY)
    with pytest.raises(ValueError):
        select_macro_asof([friday()], SATURDAY, policies={})
    with pytest.raises(ValueError):
        select_macro_asof([friday()], '2026-11-14T12:00:00', policies={'usd_index': policy()})
    with pytest.raises(ValueError):
        select_macro_asof([friday()], SATURDAY, policies={'usd_index': {}})


def test_each_required_series_is_reported_without_imputation():
    result = select_macro_asof([friday()], SATURDAY,
        policies={'usd_index': policy(), 'interest_rate': policy()})
    assert result['data_status'] == 'insufficient_data'
    assert result['series']['usd_index']['data_status'] == 'available'
    assert result['series']['interest_rate']['data_status'] == 'insufficient_data'
    assert 'value' not in result['series']['interest_rate']

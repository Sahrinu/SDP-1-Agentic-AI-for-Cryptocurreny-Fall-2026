"""Synthetic tests of explicit macro timestamp provenance; no provider calls."""
from datetime import datetime, timezone

import pytest

from market_data.macro_point_in_time import MacroPolicy, select_macro_asof


NOW=datetime(2026,11,15,12,tzinfo=timezone.utc)
POLICY=MacroPolicy(max_observation_age_days=4,max_availability_age_hours=120,calendar_timezone='UTC')
ASSUMED='assumed_next_UTC_midnight_not_verified_vintage'
LIVE='retrieved_vintage_live_only'


def record(**changes):
    return {'series':'dxy','value':103.1,'observation_date':'2026-11-13',
            'published_at':None,'available_at':'2026-11-14T00:00:00Z',
            'availability_basis':ASSUMED,'source':'Yahoo synthetic test',**changes}


def select(item,prediction=NOW):
    return select_macro_asof([item],prediction,policies={'dxy':POLICY},
                             allowed_availability_bases=('caller_attested',ASSUMED,LIVE))


def test_assumed_midnight_is_explicit_never_a_publication():
    result=select(record())
    row=result['series']['dxy']
    assert result['data_status']=='available'
    assert row['published_at'] is None
    assert row['availability_basis']==ASSUMED
    assert row['available_at']=='2026-11-14T00:00:00+00:00'
    assert row['observation_date']=='2026-11-13'
    assert row['weekend_forward_filled'] and row['observation_age_days']==2
    assert 'not independently verified' in result['limitations'][0]


@pytest.mark.parametrize('changes',[
    {'available_at':'2026-11-13T23:59:59Z'},
    {'available_at':'2026-11-14T00:00:01Z'},
    {'published_at':'2026-11-14T00:00:00Z'},
    {'availability_basis':'caller_attested'},
    {'availability_basis':'not-a-basis'},
])
def test_assumed_availability_cannot_be_silently_relabelled(changes):
    result=select(record(**changes))
    assert result['data_status']=='insufficient_data'
    assert 'value' not in result['series']['dxy']


def test_missing_publication_without_explicit_unverified_basis_is_rejected():
    item=record();del item['availability_basis']
    assert select(item)['data_status']=='insufficient_data'


def test_live_basis_requires_real_retrieval_completion_equal_to_availability():
    stamp='2026-11-15T11:59:00Z'
    item=record(availability_basis=LIVE,available_at=stamp,retrieved_at=stamp)
    row=select(item)['series']['dxy']
    assert row['data_status']=='available' and row['published_at'] is None
    assert row['availability_basis']==LIVE
    assert row['retrieved_at']==row['available_at']=='2026-11-15T11:59:00+00:00'
    for invalid in ({'retrieved_at':None},{'retrieved_at':'2026-11-15T11:58:59Z'},
                    {'published_at':'2026-11-13T14:00:00Z'}):
        assert select({**item,**invalid})['data_status']=='insufficient_data'


def test_retrieval_after_prediction_is_not_eligible():
    stamp='2026-11-15T12:00:01Z'
    result=select(record(availability_basis=LIVE,available_at=stamp,retrieved_at=stamp))
    assert result['data_status']=='insufficient_data'
    assert 'No observation available' in result['series']['dxy']['reason']


def test_same_vintage_different_provenance_is_a_conflict():
    stamp='2026-11-14T00:00:00Z'
    first=record()
    second=record(availability_basis=LIVE,available_at=stamp,retrieved_at=stamp)
    result=select_macro_asof([first,second],NOW,policies={'dxy':POLICY},
                             allowed_availability_bases=(ASSUMED,LIVE))
    assert result['data_status']=='insufficient_data'
    assert 'conflicting' in result['series']['dxy']['reason']


def test_unverified_bases_require_explicit_opt_in():
    row=select_macro_asof([record()],NOW,policies={'dxy':POLICY})['series']['dxy']
    assert row['data_status']=='insufficient_data'
    with pytest.raises(ValueError):
        select_macro_asof([record()],NOW,policies={'dxy':POLICY},allowed_availability_bases=('invalid',))
    assert select(record())['data_status']=='available'
    assert 'assumed' in select(record())['limitations'][1].lower()


def test_assumed_historical_data_rejects_excess_age_and_future_availability():
    at_boundary=datetime(2026,11,17,12,tzinfo=timezone.utc)
    stale=datetime(2026,11,18,12,tzinfo=timezone.utc)
    before_assumed_release=datetime(2026,11,13,23,59,59,tzinfo=timezone.utc)
    assert select(record(),at_boundary)['data_status']=='available'
    assert select(record(),stale)['data_status']=='insufficient_data'
    assert select(record(),before_assumed_release)['data_status']=='insufficient_data'

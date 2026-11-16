"""World Economy forecast from point-in-time validated macro observations.

Yahoo historical availability remains an explicitly labelled approximation,
not a verified publication time or vintage archive.
"""
import math

import pandas as pd
from agents._shared import cutoff_for
from market_data.macro_point_in_time import MacroPolicy, select_macro_asof

AGENT_NAME = "world_economy"
MAX_AGE_DAYS = 4
LOOKBACK_SESSIONS = 5
ALLOWED_BASES = ('caller_attested', 'assumed_next_UTC_midnight_not_verified_vintage',
                 'retrieved_vintage_live_only')


def _present(value):
    return None if value is None or pd.isna(value) else value


def _records(frame, field, forecast_date):
    """Retain observation dates and provenance for the strict selector."""
    records=[]
    for _, row in frame.iterrows():
        value=_present(row[field])
        observation=_present(row[field+'_observed_on'])
        available=_present(row[field+'_available_at'])
        if observation is None and available is None and value is None:
            continue
        if observation is not None and not isinstance(observation,str):
            # Do not silently truncate a timestamp into an observation date.
            # The selector accepts only the original YYYY-MM-DD date.
            observation=str(observation)
        # Collector blanks an already over-age forward fill; the older valid
        # observation still goes through the freshness policy below.
        if value is None and observation is not None:
            try:
                age=(forecast_date-pd.Timestamp(observation).date()).days
            except (TypeError, ValueError, OverflowError):
                age=0
            if age>MAX_AGE_DAYS:
                continue
        basis=_present(row.get(field+'_availability_basis')) or _present(row.get('availability_basis')) or 'caller_attested'
        records.append({'series':field, 'value':value, 'observation_date':observation,
            'published_at':_present(row.get(field+'_published_at')), 'available_at':available,
            'retrieved_at':_present(row.get('retrieval_completed_at')),
            'availability_basis':basis, 'source':_present(row.get('source'))})
    return records


def _select(records, field, cutoff, days, hours):
    return select_macro_asof(records, cutoff.to_pydatetime(),
        policies={field:MacroPolicy(max_observation_age_days=days,
                                    max_availability_age_hours=hours, calendar_timezone='UTC')},
        allowed_availability_bases=ALLOWED_BASES)['series'][field]


def forecast(day, data):
    """Use the selected as-of vintage for every observation in the lookback."""
    result = {'agent':AGENT_NAME, 'day':day, 'p_up':0.5,
              'evidence':[], 'data_missing':True}
    try:
        forecast_date=pd.Timestamp(day).date()
        cutoff=cutoff_for(day,data)
        frame=data.get('macro')
        if not isinstance(frame,pd.DataFrame) or frame.empty:
            raise ValueError('no macro data')
        frame=frame.copy()
        frame.index=pd.to_datetime(frame.index,utc=True)
        frame=frame[frame.index<cutoff].sort_index()
        if frame.index.has_duplicates:
            raise ValueError('duplicate macro dates')
        # A completed-day forecast uses the following UTC midnight as its
        # cutoff, while its observation age is measured on the forecast date.
        day_offset=(cutoff.date()-forecast_date).days
        if day_offset not in (0,1):
            raise ValueError('invalid macro prediction cutoff')
        changes={}
        bases=set()
        for field in ('dxy','treasury_10y'):
            records=_records(frame,field,forecast_date)
            latest=_select(records,field,cutoff,MAX_AGE_DAYS+day_offset,MAX_AGE_DAYS*24)
            if latest['data_status']!='available':
                raise ValueError(f"{field}: {latest['reason']}")
            if latest['value']<=0:
                raise ValueError(f'missing or invalid current {field}')
            age=(forecast_date-pd.Timestamp(latest['observation_date']).date()).days
            if not 0<=age<=MAX_AGE_DAYS:
                raise ValueError(f'{field} is stale ({age} calendar days)')
            observation_dates=sorted({r['observation_date'] for r in records
                if isinstance(r['observation_date'],str) and r['observation_date']<=latest['observation_date']})
            history=[]
            for observation_date in observation_dates:
                group=[r for r in records if r['observation_date']==observation_date]
                selected=_select(group,field,cutoff,14+day_offset,15*24)
                if selected['data_status']!='available':
                    if selected['reason'] in ('No observation available by prediction time',
                                              'Observation exceeds the configured freshness policy'):
                        continue
                    raise ValueError(f"{field}: {selected['reason']}")
                if selected['value']<=0:
                    raise ValueError(f'invalid {field} lookback')
                history.append(selected)
            history=history[-(LOOKBACK_SESSIONS+1):]
            if len(history)<LOOKBACK_SESSIONS+1:
                raise ValueError(f'need six distinct {field} sessions')
            if (pd.Timestamp(history[-1]['observation_date'])-
                pd.Timestamp(history[0]['observation_date'])).days>14:
                raise ValueError(f'{field} lookback spans an excessive outage')
            first,last=history[0]['value'],history[-1]['value']
            changes[field]=(last/first-1)*100 if field=='dxy' else last-first
            bases.update(item['availability_basis'] for item in history)
            result['evidence'].append(
                f"{field}: {last:g}; source date {latest['observation_date']}; "
                f"available at {latest['available_at']}; carried {age} calendar days; "
                f"availability age {latest['availability_age_hours']:.2f} hours; "
                f"availability basis {latest['availability_basis']}; "
                f"five-session change {changes[field]:+.4f} "
                f"{'percent' if field=='dxy' else 'percentage points'}")
        pressure=0.6*changes['dxy']/1.0+0.4*changes['treasury_10y']/0.25
        result['p_up']=round(0.5-0.1*math.tanh(pressure),6)
        result['data_missing']=False
        result['evidence'].append('Uncalibrated heuristic: 60% dollar / 40% yield pressure; '
            'p_up limited to 0.40-0.60. Treasury yield is a market proxy, not the Fed policy rate.')
        if 'assumed_next_UTC_midnight_not_verified_vintage' in bases:
            result['evidence'].append('Yahoo historical availability is assumed next UTC midnight; '
                'publication timestamps and historical vintages are not verified.')
        if 'retrieved_vintage_live_only' in bases:
            result['evidence'].append('Live availability is actual retrieval completion, not provider publication time; '
                'the retrieved vintage does not verify historical availability.')
        if 'caller_attested' in bases:
            result['evidence'].append('Publication and availability timestamps are caller-attested, not independently verified.')
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        result.update(p_up=0.5,data_missing=True)
        result['evidence'].append(str(exc))
    return result

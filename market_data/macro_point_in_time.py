"""Point-in-time selection of caller-supplied macroeconomic observations.

There is no provider or freshness default in the team proposal.  The caller
must supply availability provenance and an explicit policy per series; this
module never fetches, backdates, or invents observations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _aware(value):
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MacroPolicy:
    """Explicit per-series bounds: calendar observation age and availability age."""
    max_observation_age_days: int
    max_availability_age_hours: float
    calendar_timezone: str

    def __post_init__(self):
        if type(self.max_observation_age_days) is not int or self.max_observation_age_days < 0:
            raise ValueError('max_observation_age_days must be a nonnegative integer')
        hours=self.max_availability_age_hours
        if isinstance(hours,bool) or not isinstance(hours,(int,float)) or not math.isfinite(hours) or hours<=0:
            raise ValueError('max_availability_age_hours must be positive and finite')
        if not isinstance(self.calendar_timezone,str) or not self.calendar_timezone:
            raise ValueError('Explicit calendar timezone required')
        try:ZoneInfo(self.calendar_timezone)
        except (ZoneInfoNotFoundError,ValueError) as exc:raise ValueError('Invalid calendar timezone') from exc


def select_macro_asof(records, prediction_time, *, policies,
                      allowed_availability_bases=('caller_attested',)):
    """Select latest released vintages as of prediction; report each gap.

    ``records`` contain series, value, observation_date, available_at and
    source. Caller-attested publication additionally requires ``published_at``;
    Yahoo's explicitly labelled assumed historical or live-retrieval modes
    instead keep ``published_at`` null. Live retrieval mode also requires an
    aware ``retrieved_at`` equal to its post-fetch availability time. Assumed
    history must use precisely the next UTC midnight, not a claimed publication
    time. Unverified modes require explicit ``allowed_availability_bases`` opt-in;
    the default accepts only caller-attested publication timestamps. Later
    releases/revisions are ignored for this prediction. A caller
    attests all provenance; this function cannot verify a provider's release log.
    """
    prediction=_aware(prediction_time)
    if not isinstance(records,(list,tuple)) or not isinstance(policies,dict) or not policies:
        raise ValueError('Records and explicit per-series policies are required')
    if not all(isinstance(k,str) and k and isinstance(v,MacroPolicy) for k,v in policies.items()):
        raise ValueError('Every requested series needs a MacroPolicy')
    supported=('caller_attested','assumed_next_UTC_midnight_not_verified_vintage',
               'retrieved_vintage_live_only')
    if (not isinstance(allowed_availability_bases,(list,tuple,set,frozenset)) or
        not allowed_availability_bases or any(item not in supported for item in allowed_availability_bases)):
        raise ValueError('Explicit supported availability bases are required')
    results={}
    for series,policy in policies.items():
        local_date=prediction.astimezone(ZoneInfo(policy.calendar_timezone)).date()
        eligible=[]; reason=None; seen={}
        for record in records:
            if not isinstance(record,dict):
                reason='Invalid macro record';break
            if record.get('series')!=series:continue
            try:
                available=_aware(record.get('available_at'))
                if available>prediction:continue
                obs=record.get('observation_date')
                if not isinstance(obs,str) or len(obs)!=10:
                    raise ValueError('Observation date must be YYYY-MM-DD')
                obs=date.fromisoformat(obs)
                basis=record.get('availability_basis','caller_attested')
                if basis not in supported or basis not in allowed_availability_bases:
                    raise ValueError('Availability basis not explicitly permitted')
                published=None
                retrieved=None
                if basis=='caller_attested':
                    published=_aware(record.get('published_at'))
                    if published>available or obs>published.astimezone(ZoneInfo(policy.calendar_timezone)).date():
                        raise ValueError('Invalid publication order')
                else:
                    if record.get('published_at') is not None:
                        raise ValueError('Unverified Yahoo availability is not a publication timestamp')
                    if basis=='assumed_next_UTC_midnight_not_verified_vintage':
                        expected=datetime.combine(obs+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
                        if available!=expected:
                            raise ValueError('Invalid assumed historical availability')
                    else:
                        retrieved=_aware(record.get('retrieved_at'))
                        if available!=retrieved:
                            raise ValueError('Availability must equal retrieval completion')
                if obs>local_date or obs>available.astimezone(ZoneInfo(policy.calendar_timezone)).date():
                    raise ValueError('Invalid observation or availability order')
                raw=record.get('value')
                if isinstance(raw,bool):raise ValueError('Invalid value')
                value=float(raw)
                if not math.isfinite(value):raise ValueError('Invalid value')
                source=record.get('source')
                if not isinstance(source,str) or not source.strip():raise ValueError('Source missing')
                identity=(obs,available)
                comparable=(published,value,source,basis,retrieved)
                if identity in seen and seen[identity]!=comparable:
                    raise ValueError('Conflicting macro vintages')
                seen[identity]=comparable
                eligible.append((obs,available,published,value,source,basis,retrieved))
            except (ValueError,TypeError,OverflowError,OSError):
                reason='Invalid or conflicting macro observation metadata';break
        if reason:
            results[series]={'data_status':'insufficient_data','reason':reason};continue
        if not eligible:
            results[series]={'data_status':'insufficient_data','reason':'No observation available by prediction time'};continue
        obs,available,published,value,source,basis,retrieved=max(eligible,key=lambda row:(row[0],row[1]))
        age_days=(local_date-obs).days
        age_hours=(prediction-available).total_seconds()/3600
        if age_days>policy.max_observation_age_days or age_hours>policy.max_availability_age_hours:
            results[series]={'data_status':'insufficient_data','reason':'Observation exceeds the configured freshness policy',
                             'observation_date':obs.isoformat(),'observation_age_days':age_days,
                             'availability_age_hours':age_hours,'published_at':None if published is None else published.isoformat(),
                             'available_at':available.isoformat(),'source':source, 'availability_basis':basis,
                             'retrieved_at':None if retrieved is None else retrieved.isoformat()};continue
        results[series]={'data_status':'available','value':value,'source':source,
                         'observation_date':obs.isoformat(),'published_at':None if published is None else published.isoformat(),
                         'available_at':available.isoformat(),'availability_basis':basis,
                         'retrieved_at':None if retrieved is None else retrieved.isoformat(),'observation_age_days':age_days,
                         'availability_age_hours':age_hours,'forward_filled':obs<local_date,
                         'weekend_forward_filled':local_date.weekday()>=5 and obs<local_date}
    limitations=['Publication and availability provenance is caller-attested, not independently verified.']
    if any(row.get('availability_basis')=='assumed_next_UTC_midnight_not_verified_vintage' for row in results.values()):
        limitations.append('Historical availability is assumed next UTC midnight, not a verified publication or vintage time.')
    if any(row.get('availability_basis')=='retrieved_vintage_live_only' for row in results.values()):
        limitations.append('Live availability is retrieval completion, not the original provider publication time.')
    return {'prediction_time':prediction.isoformat(),'series':results,
            'data_status':'available' if all(v['data_status']=='available' for v in results.values()) else 'insufficient_data',
            'limitations':limitations}

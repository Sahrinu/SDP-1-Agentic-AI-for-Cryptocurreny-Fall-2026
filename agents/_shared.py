"""Shared validation for daily research agents; skipped by agent discovery."""

import math

import pandas as pd


def response(name, day, probability=0.5, evidence=(), *, missing=False):
    return {"agent": name, "day": day, "p_up": round(float(probability), 6),
            "evidence": list(evidence), "data_missing": missing}


def cutoff_for(day, data=None):
    if data is not None and data.get("_decision_time") is not None:
        stamp = pd.Timestamp(data["_decision_time"])
        if stamp.tzinfo is None or stamp.tz_convert("UTC").date() != pd.Timestamp(day).date():
            raise ValueError("Live decision timestamp must be aware and match the UTC forecast date")
        return stamp.tz_convert("UTC")
    return pd.Timestamp(pd.Timestamp(day).date(), tz="UTC") + pd.Timedelta(days=1)


def published_history(day, data, key, fields, *, max_age):
    """Select complete rows actually available by the completed-day boundary.

    Duplicate observation dates are permitted for revisions: select only the
    latest available vintage per date. Never use a later vintage in a replay.
    """
    frame = data.get(key)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"no {key} data")
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, utc=True)
    cutoff = cutoff_for(day, data)
    frame = frame[frame.index < cutoff]
    if frame.empty:
        raise ValueError(f"no historical {key} rows")
    required = {*fields, "available_at", "complete", "source"}
    if not required <= set(frame):
        raise ValueError(f"{key} needs columns: {', '.join(sorted(required))}")
    times = []
    for raw in frame.available_at:
        value = pd.Timestamp(raw)
        if pd.isna(value) or value.tzinfo is None:
            raise ValueError(f"{key} available_at must include timezone")
        times.append(value.tz_convert("UTC"))
    frame["available_at"] = times
    frame = frame[(frame.available_at <= cutoff) & (frame.available_at >= frame.index)]
    if not frame.complete.map(lambda x: str(x).lower() in {"true", "false"}).all():
        raise ValueError(f"{key} complete must be true or false")
    frame = frame[frame.complete.map(lambda x: str(x).lower() == "true")]
    frame["_date"] = frame.index.normalize()
    frame = frame.sort_values(["_date", "available_at"]).drop_duplicates("_date", keep="last")
    if frame.empty:
        raise ValueError(f"no complete published {key} observations")
    if (pd.Timestamp(day).date() - frame.index[-1].date()).days > max_age:
        raise ValueError(f"Latest complete {key} observation is {frame.index[-1].date()} UTC; maximum age is {max_age} calendar days")
    for field in fields:
        if frame[field].map(lambda x: isinstance(x, bool)).any():
            raise ValueError(f"{field} cannot be boolean")
        frame[field] = pd.to_numeric(frame[field], errors="raise")
        if not all(math.isfinite(float(x)) for x in frame[field]):
            raise ValueError(f"{field} must be finite")
    if not frame.source.map(lambda x: isinstance(x, str) and bool(x.strip())).all():
        raise ValueError(f"{key} needs a named source")
    return frame


def daily_prices(day, data, *, minimum, full=False):
    frame = data.get("btc_prices")
    if not isinstance(frame, pd.DataFrame) or "close" not in frame:
        raise ValueError("need btc_prices close history")
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, utc=True).normalize()
    boundary = cutoff_for(day, data).normalize()
    frame = frame[frame.index < boundary].sort_index()
    if frame.index.has_duplicates or len(frame) < minimum:
        raise ValueError(f"need {minimum} unique daily BTC closes")
    expected = boundary - pd.Timedelta(days=1)
    if frame.index[-1] != expected:
        raise ValueError(f"Latest completed BTC candle is {frame.index[-1].date()}; expected {expected.date()} UTC")
    window = frame if full else frame.tail(minimum)
    if any((b - a).days != 1 for a, b in zip(window.index, window.index[1:])):
        raise ValueError("BTC lookback must have consecutive UTC days")
    values = pd.to_numeric(window.close, errors="raise")
    if not all(math.isfinite(float(x)) and x > 0 for x in values):
        raise ValueError("BTC closes must be positive and finite")
    return values

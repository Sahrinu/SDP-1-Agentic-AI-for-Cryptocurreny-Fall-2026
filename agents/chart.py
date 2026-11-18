"""A daily probability adapter alongside Student 1's unchanged chart report."""

import math
import pandas as pd
from agents._shared import cutoff_for, daily_prices, response

AGENT_NAME = "chart"


def _recent_closes(day, data, key, *, count, step, max_age):
    frame = data.get(key)
    if not isinstance(frame, pd.DataFrame) or 'close' not in frame:
        raise ValueError(f'Missing {key} completed candles')
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, utc=True)
    cutoff = cutoff_for(day, data)
    frame = frame[frame.index < cutoff].sort_index().tail(count)
    if len(frame) != count or frame.index.has_duplicates:
        raise ValueError(f'Need {count} unique completed {key} candles')
    if any(b - a != step for a, b in zip(frame.index, frame.index[1:])):
        raise ValueError(f'{key} candles have a gap')
    if cutoff - frame.index[-1] > max_age:
        raise ValueError(f'{key} latest candle is stale')
    values = pd.to_numeric(frame.close, errors='raise')
    if not all(math.isfinite(float(value)) and value > 0 for value in values):
        raise ValueError(f'{key} closes must be positive and finite')
    return values


def forecast(day, data):
    try:
        close = daily_prices(day, data, minimum=70)
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        histogram = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
        pressure = .7 * (ema20 / ema50 - 1) / .03 + .3 * histogram / float(close.iloc[-1]) / .005
        evidence = [
            f"70 completed daily closes through {close.index[-1].date()}; EMA20 {ema20:.2f}; EMA50 {ema50:.2f}; MACD histogram {histogram:.4f}",
        ]
        if 'btc_hourly' in data or 'btc_minute' in data:
            hourly = _recent_closes(day, data, 'btc_hourly', count=25,
                step=pd.Timedelta(hours=1), max_age=pd.Timedelta(hours=1, minutes=2))
            minute = _recent_closes(day, data, 'btc_minute', count=61,
                step=pd.Timedelta(minutes=1), max_age=pd.Timedelta(minutes=3))
            hourly_return = float(hourly.iloc[-1] / hourly.iloc[-25] - 1)
            minute_return = float(minute.iloc[-1] / minute.iloc[-61] - 1)
            signal = (.5 * math.tanh(pressure)
                      + .35 * math.tanh(hourly_return / .03)
                      + .15 * math.tanh(minute_return / .01))
            evidence.extend([
                f"25 completed hourly candles through {hourly.index[-1].isoformat()}; 24h return {hourly_return:+.4%}",
                f"61 completed minute candles through {minute.index[-1].isoformat()}; 60m return {minute_return:+.4%}",
                "Experimental daily/hourly/minute blend: 50% daily trend, 35% hourly return, 15% minute return; not calibrated.",
            ])
        else:
            signal = math.tanh(pressure)
            evidence.append('Daily-only rule: intraday candles were not supplied for this replay or live forecast.')
        evidence.append('Uncalibrated trend/momentum probability adapter in [0.38,0.62]; separate from the descriptive Chart report.')
        return response(AGENT_NAME, day, .5 + .12 * signal, evidence)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

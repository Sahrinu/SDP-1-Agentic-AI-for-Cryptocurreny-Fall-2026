"""A daily probability adapter alongside Student 1's unchanged chart report."""

import math
from agents._shared import daily_prices, response

AGENT_NAME = "chart"


def forecast(day, data):
    try:
        close = daily_prices(day, data, minimum=70)
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        histogram = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
        pressure = .7 * (ema20 / ema50 - 1) / .03 + .3 * histogram / float(close.iloc[-1]) / .005
        return response(AGENT_NAME, day, .5 + .12 * math.tanh(pressure), [
            f"70 completed daily closes through {close.index[-1].date()}; EMA20 {ema20:.2f}; EMA50 {ema50:.2f}; MACD histogram {histogram:.4f}",
            "Uncalibrated trend/momentum probability adapter in [0.38,0.62]; separate from the descriptive Chart report."])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

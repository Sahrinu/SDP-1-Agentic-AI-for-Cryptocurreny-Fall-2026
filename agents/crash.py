"""Historical tail-loss and drawdown warning; no claim of calibrated crash odds."""

import math

from agents._shared import daily_prices, response

AGENT_NAME = "crash"


def forecast(day, data):
    try:
        close = daily_prices(day, data, minimum=91)
        returns = close.pct_change().dropna()
        # Today's return is held out of the historical tail threshold.
        baseline = returns.iloc[:-1]
        tail = float(baseline.quantile(0.05))
        today = float(returns.iloc[-1])
        drawdown = float(close.iloc[-1] / close.tail(30).max() - 1)
        tail_scale = max(0.02, abs(min(0.0, tail)))
        shock = max(0.0, -today / tail_scale)
        pressure = 0.6 * shock + 0.4 * max(0.0, -drawdown / 0.10)
        return response(AGENT_NAME, day, 0.5 - 0.15 * math.tanh(pressure), [
            f"91 completed daily closes through {close.index[-1].date()}; historical 5% return quantile {tail:+.4%}",
            f"Latest daily return {today:+.4%}; 30-day peak drawdown {drawdown:+.4%}",
            "Uncalibrated downside-pressure rule, p_up 0.35–0.50. Historical extreme moves do not establish next-day crash probability.",
        ])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

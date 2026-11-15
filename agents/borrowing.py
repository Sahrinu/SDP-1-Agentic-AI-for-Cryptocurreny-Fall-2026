"""Funding/open-interest crowding heuristic, not a trained forecast model."""

import math

from agents._shared import published_history, response

AGENT_NAME = "borrowing"


def forecast(day, data):
    try:
        history = published_history(day, data, "borrowing",
                                    ["funding_rate", "open_interest_usd"], max_age=1).tail(6)
        if len(history) != 6 or any((b - a).days != 1 for a, b in zip(history.index, history.index[1:])):
            raise ValueError("need six consecutive completed borrowing days")
        if (history.open_interest_usd <= 0).any() or (history.funding_rate.abs() > 0.05).any():
            raise ValueError("invalid OI or funding units; funding must be a decimal per 8-hour settlement")
        current = history.iloc[-1]
        growth = float(current.open_interest_usd / history.iloc[0].open_interest_usd - 1)
        funding = float(history.funding_rate.tail(3).mean())
        # Positive funding indicates crowded longs; OI expansion amplifies the
        # direction of that crowding, not an unconditional bullish OI signal.
        crowding = funding / 0.0001 * (1 + min(2.0, max(0.0, growth) / 0.10))
        p = 0.5 - 0.12 * math.tanh(crowding)
        return response(AGENT_NAME, day, p, [
            f"Source {current.source}; observation {history.index[-1].date()}; available {current.available_at.isoformat()}",
            f"Three-day mean 8h funding {funding:.8f}; five-day OI change {growth:+.4%}",
            "Uncalibrated contrarian crowding rule; p_up 0.38–0.62; USDT OI is a USD proxy.",
        ])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

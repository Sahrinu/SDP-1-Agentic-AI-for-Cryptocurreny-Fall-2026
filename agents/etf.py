"""Published US spot-Bitcoin ETF net-flow research heuristic."""

import math

from agents._shared import published_history, response

AGENT_NAME = "etf"


def forecast(day, data):
    try:
        history = published_history(day, data, "etf_flows", ["net_flow_usd"], max_age=4).tail(3)
        if len(history) != 3 or (history.index[-1] - history.index[0]).days > 7:
            raise ValueError("need three recent complete ETF reporting sessions")
        mean_flow = float(history.net_flow_usd.mean())
        current = history.iloc[-1]
        return response(AGENT_NAME, day, 0.5 + 0.10 * math.tanh(mean_flow / 500_000_000), [
            f"Source {current.source}; latest flow date {history.index[-1].date()}; available {current.available_at.isoformat()}",
            f"Mean net flow across three published sessions: USD {mean_flow:,.2f}",
            "Uncalibrated flow heuristic, p_up 0.40–0.60. Complete aggregate US spot BTC ETF flows only; not ETF price returns.",
            "Carries published evidence across non-reporting days for at most four calendar days; never invents zero weekend flows.",
        ])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

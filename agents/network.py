"""Daily Bitcoin network-usage research heuristic."""

import math
from agents._shared import published_history, response

AGENT_NAME = "network"


def forecast(day, data):
    try:
        history = published_history(day, data, "network", ["active_addresses", "transactions"], max_age=4).tail(8)
        if len(history) < 8 or any((b-a).days != 1 for a,b in zip(history.index, history.index[1:])):
            raise ValueError("Need eight consecutive published network days")
        if (history[["active_addresses", "transactions"]] <= 0).any().any():
            raise ValueError("Network counts must be positive")
        changes = {key: float(history[key].iloc[-1] / history[key].iloc[:-1].mean() - 1)
                   for key in ("active_addresses", "transactions")}
        p = .5 + .10 * math.tanh((changes["active_addresses"] + changes["transactions"]) / .20)
        return response(AGENT_NAME, day, p, [
            f"Source {history.iloc[-1].source}; latest network day {history.index[-1].date()}; available {history.iloc[-1].available_at.isoformat()}",
            f"Latest versus prior seven days: active addresses {changes['active_addresses']:+.2%}, transactions {changes['transactions']:+.2%}",
            "Uncalibrated usage heuristic in [0.40,0.60]; activity does not establish demand or next-day returns."])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

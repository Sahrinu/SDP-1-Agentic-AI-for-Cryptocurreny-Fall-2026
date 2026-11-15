"""Published Bitcoin hash-rate/fee research heuristic."""

import math
from agents._shared import published_history, response

AGENT_NAME = "miner"


def forecast(day, data):
    try:
        history = published_history(day, data, "miner", ["hash_rate", "fees_btc"], max_age=4).tail(8)
        if len(history) < 8 or any((b-a).days != 1 for a,b in zip(history.index, history.index[1:])):
            raise ValueError("Need eight consecutive published miner days")
        if (history.hash_rate <= 0).any() or (history.fees_btc < 0).any():
            raise ValueError("Invalid hash rate or fees")
        hash_change = float(history.hash_rate.iloc[-1] / history.hash_rate.iloc[:-1].mean() - 1)
        baseline_fee = float(history.fees_btc.iloc[:-1].mean())
        fee_change = float(history.fees_btc.iloc[-1] / baseline_fee - 1) if baseline_fee else 0.0
        p = .5 + .08 * math.tanh(.7 * hash_change / .10 + .3 * fee_change / .50)
        return response(AGENT_NAME, day, p, [
            f"Source {history.iloc[-1].source}; latest miner day {history.index[-1].date()}; available {history.iloc[-1].available_at.isoformat()}",
            f"Latest versus prior seven days: hash rate {hash_change:+.2%}, total fees {fee_change:+.2%}",
            "Uncalibrated miner-health proxy in [0.42,0.58]; does not measure miners' actual BTC sales."])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

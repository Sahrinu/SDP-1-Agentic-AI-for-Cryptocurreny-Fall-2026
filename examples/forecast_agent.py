"""Example adapter for the daily replay's forecast(day, data) contract.

Copy this module into a directory passed with --agents-dir, then replace the
illustrative rule with a validated agent. The input tables contain rows no later
than day. The forecast targets the next available BTC close in the price CSV.
"""

AGENT_NAME = "example_momentum"


def forecast(day, data):
    prices = data.get("btc_prices")
    if prices is None or len(prices) < 2:
        return {"agent": AGENT_NAME, "day": day, "p_up": 0.5,
                "evidence": [], "data_missing": True}
    close = prices["close"]
    rose = close.iloc[-1] > close.iloc[-2]
    return {"agent": AGENT_NAME, "day": day, "p_up": 0.55 if rose else 0.45,
            "evidence": [f"latest close {close.iloc[-1]:.2f}"], "data_missing": False}

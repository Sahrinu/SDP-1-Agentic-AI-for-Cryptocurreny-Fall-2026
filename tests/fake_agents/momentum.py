# Fake agent for testing: up yesterday -> leans up.
AGENT_NAME = "fake_momentum"


def forecast(day, data):
    close = data["btc_prices"]["close"]
    p = 0.6 if close.iloc[-1] > close.iloc[-2] else 0.4
    return {"agent": AGENT_NAME, "day": day, "p_up": p, "evidence": [], "data_missing": False}

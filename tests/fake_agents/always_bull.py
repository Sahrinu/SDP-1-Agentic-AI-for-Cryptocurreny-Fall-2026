# Fake agent for testing: always says up with 70%. Should lose coins on down days.
AGENT_NAME = "fake_always_bull"


def forecast(day, data):
    return {"agent": AGENT_NAME, "day": day, "p_up": 0.7, "evidence": [], "data_missing": False}

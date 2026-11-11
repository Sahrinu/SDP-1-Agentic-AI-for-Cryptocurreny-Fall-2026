# Fake agent for testing: has no data on Mondays, to check that missing data becomes 0.5.
import pandas as pd

AGENT_NAME = "fake_missing_data"


def forecast(day, data):
    if pd.Timestamp(day).weekday() == 0:
        return {"agent": AGENT_NAME, "day": day, "p_up": 0.5, "evidence": [], "data_missing": True}
    return {"agent": AGENT_NAME, "day": day, "p_up": 0.45, "evidence": [], "data_missing": False}

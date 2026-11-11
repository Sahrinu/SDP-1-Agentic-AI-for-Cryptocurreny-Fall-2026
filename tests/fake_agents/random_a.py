# Fake agent for testing: random guess between 0.3 and 0.7 (same guess every run).
import random

AGENT_NAME = "fake_random_a"


def forecast(day, data):
    p = random.Random(AGENT_NAME + day).uniform(0.3, 0.7)
    return {"agent": AGENT_NAME, "day": day, "p_up": p, "evidence": [], "data_missing": False}

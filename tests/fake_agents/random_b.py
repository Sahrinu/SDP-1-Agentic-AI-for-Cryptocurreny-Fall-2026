# Fake agent for testing: another random guess between 0.3 and 0.7.
import random

AGENT_NAME = "fake_random_b"


def forecast(day, data):
    p = random.Random(AGENT_NAME + day).uniform(0.3, 0.7)
    return {"agent": AGENT_NAME, "day": day, "p_up": p, "evidence": [], "data_missing": False}

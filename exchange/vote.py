# Majority vote: each agent votes up (p_up > 0.5) or down (p_up < 0.5).
# Agents at exactly 0.5 don't vote. The team forecast is the share of up votes.


def vote(forecasts):
    ups = sum(1 for p in forecasts.values() if p > 0.5)
    downs = sum(1 for p in forecasts.values() if p < 0.5)
    if ups + downs == 0:
        return 0.5
    return ups / (ups + downs)

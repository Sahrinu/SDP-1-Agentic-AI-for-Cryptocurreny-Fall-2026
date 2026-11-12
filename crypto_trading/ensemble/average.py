# Simple average: every agent counts the same.


def average(forecasts):
    if not forecasts:
        return 0.5
    return sum(forecasts.values()) / len(forecasts)

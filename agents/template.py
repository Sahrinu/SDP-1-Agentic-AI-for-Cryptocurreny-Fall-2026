# Template for every agent in the Agent Exchange.
# Copy this file, rename it (e.g. chart.py) and change the logic inside forecast().
#
# Rules:
# - data only contains rows dated on or before `day` (run.py cuts it for you)
# - p_up is the chance Bitcoin closes higher tomorrow, between 0.05 and 0.95
# - if your data is missing for that day, return p_up = 0.5 and data_missing = True
#
# What is inside `data`:
#   data["btc_prices"], data["fear_greed"], ...  one pandas DataFrame per CSV in data/,
#                                               indexed by date
#   data["news_dir"]                             path to the data/news folder
#   data["fake_headline"]                        None on normal runs, a string on attack runs
#                                               (only the News agent should use this)

AGENT_NAME = "template"


def forecast(day, data):
    prices = data.get("btc_prices")
    if prices is None or len(prices) == 0:
        return {"agent": AGENT_NAME, "day": day, "p_up": 0.5,
                "evidence": ["no price data"], "data_missing": True}

    # example rule: price went up today -> slightly more likely to go up tomorrow
    close = prices["close"]
    went_up_today = close.iloc[-1] > close.iloc[-2]
    p_up = 0.55 if went_up_today else 0.45

    return {
        "agent": AGENT_NAME,
        "day": day,
        "p_up": p_up,
        "evidence": [f"close today {close.iloc[-1]:.0f}, yesterday {close.iloc[-2]:.0f}"],
        "data_missing": False,
    }

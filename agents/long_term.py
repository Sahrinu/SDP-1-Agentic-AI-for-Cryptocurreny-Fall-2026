"""Walk-forward residual-neighbour frequency, not a power-law price guarantee."""

import math
from datetime import date
from agents._shared import daily_prices, response

AGENT_NAME = "long_term"


def forecast(day, data):
    try:
        close = daily_prices(day, data, minimum=791, full=True).tail(1096)
        sx = sy = sxx = sxy = 0.0
        residuals = []
        for i, (stamp, price) in enumerate(close.items()):
            age = (stamp.date() - date(2009, 1, 3)).days
            if age <= 0:
                raise ValueError("Price dates must follow Bitcoin genesis")
            x, y = math.log(age), math.log(float(price))
            sx += x; sy += y; sxx += x*x; sxy += x*y
            n = i+1
            if i >= 729:
                variance = sxx - sx*sx/n
                if variance <= 1e-12:
                    raise ValueError("Insufficient log-time variation")
                slope = (sxy - sx*sy/n) / variance
                intercept = (sy - slope*sx) / n
                residuals.append((i, y - intercept - slope*x))
        current = residuals[-1][1]
        historical = [(abs(r-current), int(close.iloc[i+1] > close.iloc[i]))
                      for i,r in residuals[:-1]]
        if len(historical) < 60:
            raise ValueError("Need 60 settled walk-forward comparison days after the initial two-year fit")
        neighbours = sorted(historical, key=lambda x:x[0])[:60]
        wins = sum(y for _,y in neighbours)
        p = (wins + 5) / (len(neighbours) + 10)
        return response(AGENT_NAME, day, p, [
            f"{len(close)} completed daily closes through {close.index[-1].date()}; current log-price residual {current:+.5f}",
            f"60 nearest previously observed residuals had {wins} higher next closes; frequency shrunk with ten neutral pseudo-observations.",
            "Each historical trend is fitted only through its own date; neighbour labels stop before the current forecast. Experimental daily adapter, not a validated power-law trading edge."])
    except (ValueError, KeyError, TypeError, OverflowError, IndexError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

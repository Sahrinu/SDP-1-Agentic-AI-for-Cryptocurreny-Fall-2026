"""Point-in-time daily adapter; probabilities are uncalibrated heuristics."""

import math

import pandas as pd

from .data import COINS


def forecast(day, data):
    result = dict(agent="stablecoin", day=day, p_up=0.5, evidence=[], data_missing=True)
    try:
        cutoff = pd.Timestamp(day).normalize().tz_localize("UTC") + pd.Timedelta(days=1)
        frame = data.get("stablecoin")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("No archived stablecoin data")
        frame = frame.copy()
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame = frame[frame.index < cutoff]
        for column in ("observed_at", "available_at"):
            if any(pd.Timestamp(value).tzinfo is None for value in frame[column]):
                raise ValueError("Archive timestamps require timezone")
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
        frame = frame[(frame.observed_at < cutoff) & (frame.available_at < cutoff)]
        frame = frame.sort_values("observed_at")
        if frame.index.has_duplicates:
            raise ValueError("Duplicate stablecoin daily rows")
        if frame.empty:
            raise ValueError("No stablecoin snapshot available at decision time")
        current = frame.iloc[-1]
        if any(index.normalize() != row.observed_at.normalize() for index, row in frame.iterrows()):
            raise ValueError("Archive date must match observation UTC date")
        age = (cutoff - current.observed_at).total_seconds()
        if not 0 < age <= 900:
            raise ValueError("Stablecoin snapshot must be within 15 minutes of UTC day end")

        def values(row):
            prices, supply = [], 0.0
            if not isinstance(row["source"], str) or not row["source"].strip():
                raise ValueError("Missing stablecoin source")
            if row.available_at < row.observed_at:
                raise ValueError("Availability precedes observation")
            for symbol in COINS.values():
                prefix = symbol.lower()
                price, cap = row[prefix + "_price_usd"], row[prefix + "_market_cap_usd"]
                if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) <= 0
                       for v in (price, cap)):
                    raise ValueError("Invalid stablecoin price or market cap")
                quoted = pd.Timestamp(row[prefix + "_updated_at"])
                if quoted.tzinfo is None:
                    raise ValueError("Quote timestamps require timezone")
                if not 0 <= (row.observed_at - quoted).total_seconds() <= 900:
                    raise ValueError("Stale or future stablecoin quote")
                prices.append(float(price))
                supply += float(cap) / float(price)
            if not math.isfinite(supply) or supply <= 0:
                raise ValueError("Invalid aggregate supply proxy")
            return prices, supply

        prices, supply = values(current)
        worst = max(abs(p - 1) * 100 for p in prices)
        result["evidence"].append(f"Source {current.source}; observed {current.observed_at.isoformat()}; worst peg deviation {worst:.4f}%")
        probability = 0.5
        if worst >= 2:
            probability = 0.4
            result["evidence"].append("Single-provider peg stress; not a confirmed multi-source incident")
        elif worst >= 0.5:
            result["evidence"].append("Peg warning; liquidity signal suppressed")
        else:
            if len(frame) < 2:
                raise ValueError("Need a preceding daily stablecoin snapshot")
            previous = frame.iloc[-2]
            elapsed = (current.observed_at - previous.observed_at).total_seconds() / 3600
            if not 23.75 <= elapsed <= 24.25 or current.source != previous.source:
                raise ValueError("Need comparable same-source snapshots approximately 24 hours apart")
            old_prices, old_supply = values(previous)
            if max(abs(p - 1) * 100 for p in old_prices) >= 0.5:
                raise ValueError("Baseline has peg stress")
            for symbol in COINS.values():
                field = symbol.lower() + "_updated_at"
                if pd.Timestamp(current[field]) <= pd.Timestamp(previous[field]):
                    raise ValueError("Provider quotes did not advance")
            change = (supply / old_supply - 1) * 100
            probability = 0.5 + 0.1 * math.tanh(change)
            result["evidence"].append(f"Price-adjusted supply proxy change {change:+.4f}% over {elapsed:.2f}h; not measured mint/burn or exchange inflow")
        result.update(p_up=round(probability, 6), data_missing=False)
        result["evidence"].append("Uncalibrated heuristic in [0.40, 0.60]; one stablecoin vote")
    except (KeyError, TypeError, ValueError, OverflowError):
        result.update(p_up=0.5, data_missing=True)
        result["evidence"].append("Missing, invalid, stale, or incomparable archived stablecoin evidence")
    return result

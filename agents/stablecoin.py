"""Stablecoin agent: issuance-asymmetry liquidity signal (DefiLlama, 2019+) with a peg-stress
override (CoinGecko); archived 24h snapshot rule as fallback."""

import json
import math
from pathlib import Path

import pandas as pd
from agents._shared import cutoff_for

from market_data.stablecoin.data import COINS


AGENT_NAME = "stablecoin"


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MODEL_PATH = DATA_DIR / "stablecoin_depeg_model.json"
LIQUIDITY_PATH = DATA_DIR / "stablecoin_liquidity_model.json"
WEEK_TO_DAY = 0.2   # no 24h skill in any test year; pass on only a small share of the 7d view


def _model(data, key, path):
    model = data.get(key)
    if model is None and path.is_file():
        model = json.loads(path.read_text(encoding="utf-8"))
    return model


def _history(day, data, model, key="stablecoin_history"):
    history = data.get(key)
    if not isinstance(history, pd.DataFrame) or history.empty or not model:
        return None
    cutoff = cutoff_for(day, data)
    if data.get("_decision_time") is None and cutoff.date() <= pd.Timestamp(model["train_end"]).date() + pd.Timedelta(days=1):
        return None  # replay day inside the training window: refuse in-sample evidence
    frame = history.copy()
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame[frame.index < cutoff].sort_index()
    if len(frame) < 40 or (cutoff - frame.index[-1]) > pd.Timedelta(days=2):
        return None
    return frame


def liquidity_forecast(day, data):
    """Issuance asymmetry (abnormal issuance, mint after dip), USDT premium, dominance."""
    from market_data.stablecoin.liquidity_model import predict
    model = _model(data, "stablecoin_liquidity_model", LIQUIDITY_PATH)
    frame = _history(day, data, model, key="stablecoin_history_long")
    if frame is None:
        return None
    _, p7, score, parts = predict(model, frame)
    probability = 0.5 + WEEK_TO_DAY * (p7 - 0.5)
    drivers = sorted(parts, key=lambda k: -abs(parts[k]))[:3]
    summary = model.get("summary", {}).get("h7", {})
    evidence = [
        f"Liquidity 7-day view: P(BTC higher in 7d) = {p7:.1%} (score {score:+.2f}; "
        + ", ".join(f"{k} {parts[k]:+.2f}" for k in drivers) + ")",
        f"Weak, slow signal: 24h vote uses {WEEK_TO_DAY:.0%} of the 7-day view"
        + (f"; walk-forward {summary['years']} years: rank IC positive in {summary['years_ic_positive']}"
           f" (mean {summary['mean_ic']:+.2f}), pooled 7d Brier {summary['pooled_brier']:.3f} vs coin flip 0.250"
           if summary else ""),
    ]
    return probability, p7, evidence


def peg_risk(day, data):
    """Learned next-day peg-stress risk; used as a risk override, not a direction signal."""
    from market_data.stablecoin.depeg_model import predict
    model = _model(data, "stablecoin_depeg_model", MODEL_PATH)
    frame = _history(day, data, model)
    if frame is None:
        return None
    risk, _, top = predict(model, frame)
    base = model["base_rate"]
    return risk, base, max(0.0, (risk - base) / (1 - base)), top


def forecast(day, data):
    try:
        liquidity = liquidity_forecast(day, data)
    except (KeyError, TypeError, ValueError, OverflowError):
        liquidity = None
    try:
        stress = peg_risk(day, data)
    except (KeyError, TypeError, ValueError, OverflowError):
        stress = None
    if liquidity is None:
        return rule_forecast(day, data)  # fall back to the archived 24h snapshot rule
    probability, p7, evidence = liquidity
    if stress is not None:
        risk, base, excess, top = stress
        evidence.append(f"Peg-stress risk {risk:.1%} vs base {base:.1%} (drivers {', '.join(top)})")
        if excess >= 0.5:
            probability = min(probability, 0.45)
            evidence.append("High peg stress overrides liquidity: capped bearish")
    probability = min(0.6, max(0.4, probability))
    return dict(agent="stablecoin", day=day, p_up=round(probability, 6), p_up_7d=round(p7, 6),
                evidence=evidence, data_missing=False)


def rule_forecast(day, data):
    result = dict(agent="stablecoin", day=day, p_up=0.5, evidence=[], data_missing=True)
    try:
        cutoff = cutoff_for(day, data)
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
            raise ValueError("Stablecoin snapshot must be within 15 minutes of the decision")

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

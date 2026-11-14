"""Student 5 macro forecast; filename retains the requested 'econompy' spelling.

Transparent, uncalibrated classroom heuristic, not a trained probability model.
The forecast is made after the completed UTC day, matching the replay template.
"""

import math

import pandas as pd

AGENT_NAME = "world_economy"
MAX_AGE_DAYS = 4
LOOKBACK_SESSIONS = 5


def forecast(day, data):
    """Consume data['macro']; return exactly the template's public fields."""
    result = {"agent": AGENT_NAME, "day": day, "p_up": 0.5,
              "evidence": [], "data_missing": True}
    try:
        day_date = pd.Timestamp(day).date()
        cutoff = pd.Timestamp(day_date, tz="UTC") + pd.Timedelta(days=1)
        frame = data.get("macro")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("no macro data")
        frame = frame.copy()
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame = frame[frame.index < cutoff].sort_index()
        if frame.index.has_duplicates:
            raise ValueError("duplicate macro dates")
        changes = {}
        for field in ("dxy", "treasury_10y"):
            observed = field + "_observed_on"
            available = field + "_available_at"
            values = pd.to_numeric(frame[field], errors="coerce")
            dates = pd.to_datetime(frame[observed], utc=True, errors="coerce")
            releases = pd.to_datetime(frame[available], utc=True, errors="coerce")
            # Filter again here: callers other than run.py may pass future rows.
            eligible = frame[(releases <= cutoff) & (dates < cutoff)].copy()
            eligible[field] = values.loc[eligible.index]
            eligible[observed] = dates.loc[eligible.index]
            eligible[available] = releases.loc[eligible.index]
            if eligible.empty:
                raise ValueError(f"no available {field} observations")
            latest = eligible.iloc[-1]
            if not math.isfinite(float(latest[field])) or latest[field] <= 0:
                raise ValueError(f"missing or invalid current {field}")
            age = (day_date - latest[observed].date()).days
            if not 0 <= age <= MAX_AGE_DAYS:
                raise ValueError(f"{field} is stale ({age} calendar days)")
            # Repeated Friday values on Saturday/Sunday are not new sessions.
            history = eligible.drop_duplicates(observed, keep="last").sort_values(observed)
            history = history.tail(LOOKBACK_SESSIONS + 1)
            if len(history) < LOOKBACK_SESSIONS + 1:
                raise ValueError(f"need six distinct {field} sessions")
            if not all(math.isfinite(float(x)) and x > 0 for x in history[field]):
                raise ValueError(f"invalid {field} lookback")
            if (history.iloc[-1][observed] - history.iloc[0][observed]).days > 14:
                raise ValueError(f"{field} lookback spans an excessive outage")
            first, last = float(history.iloc[0][field]), float(history.iloc[-1][field])
            changes[field] = (last / first - 1) * 100 if field == "dxy" else last - first
            result["evidence"].append(
                f"{field}: {last:g}; source date {latest[observed].date()}; "
                f"available at {latest[available].isoformat()}; carried {age} calendar days; "
                f"five-session change {changes[field]:+.4f} "
                f"{'percent' if field == 'dxy' else 'percentage points'}"
            )
        # Stronger dollar/rising yields are treated as macro headwinds. These
        # coefficients are explicit assumptions, not fitted predictive evidence.
        pressure = 0.6 * changes["dxy"] / 1.0 + 0.4 * changes["treasury_10y"] / 0.25
        result["p_up"] = round(0.5 - 0.1 * math.tanh(pressure), 6)
        result["data_missing"] = False
        result["evidence"].append(
            "Uncalibrated heuristic: 60% dollar / 40% yield pressure; p_up limited "
            "to 0.40–0.60. Treasury yield is a market proxy, not the Fed policy rate."
        )
        result["evidence"].append(
            "Yahoo historical daily availability is an assumed next-UTC-midnight "
            "cutoff, not a verified publication timestamp or vintage archive."
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        result.update(p_up=0.5, data_missing=True)
        result["evidence"].append(str(exc))
    return result

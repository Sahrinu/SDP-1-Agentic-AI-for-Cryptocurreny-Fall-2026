"""Daily US spot-Bitcoin ETF net flows from the SoSoValue API, merged into data/etf_flows.csv.

The free key returns only about the last month (older dates answer HTTP 403),
so this keeps the file current; older days (e.g. the replay window) come from
the reviewed Farside rows already in the file, which are never overwritten.

available_at: SoSoValue publishes a day's total after US settlement. A newly
seen row is stamped at the estimated publication time (12:00 UTC the next
day), or at the retrieval time if that is earlier, so no row is ever marked
available before it could have been seen. retrieved_at records when it was
actually downloaded.

CLI:  python -m market_data.etf_sosovalue [--env-file .env] [--csv data/etf_flows.csv]
"""

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

URL = "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=BTC&country_code=US&limit=300"
SOURCE = "SoSoValue API etfs/summary-history (US BTC spot, total_net_inflow USD)"
PUBLICATION_LAG = pd.Timedelta(hours=36)  # date 00:00 UTC -> 12:00 UTC the next day
COLUMNS = ["date", "net_flow_usd", "available_at", "complete", "source", "retrieved_at"]


def api_key(env_file=None):
    import os
    key = os.environ.get("SOSOVALUE_API_KEY")
    if not key and env_file is not None and Path(env_file).is_file():
        from dotenv import dotenv_values
        key = dotenv_values(env_file).get("SOSOVALUE_API_KEY")  # not exported to os.environ
    return key or None


def fetch(key, *, get=None, now=None):
    """Recent daily totals as rows in the etf_flows.csv schema."""
    if not key:
        raise ValueError("SOSOVALUE_API_KEY is not configured")
    if get is None:
        from market_data.http import get_json
        get = lambda url, headers: get_json(url, timeout=30, headers=headers)
    payload = get(URL, {"x-soso-api-key": key})
    if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), list):
        raise ValueError("SoSoValue returned an unexpected response")
    retrieved = pd.Timestamp(now or datetime.now(timezone.utc))
    if retrieved.tzinfo is None:
        raise ValueError("retrieval time needs a timezone")
    retrieved = retrieved.tz_convert("UTC")
    rows = []
    for item in payload["data"]:
        day = pd.Timestamp(item["date"], tz="UTC")
        if day.normalize() != day or day > retrieved:
            raise ValueError("invalid SoSoValue date")
        flow = item.get("total_net_inflow")
        complete = isinstance(flow, (int, float)) and not isinstance(flow, bool) and math.isfinite(flow)
        rows.append({
            "date": day.strftime("%Y-%m-%d"),
            "net_flow_usd": float(flow) if complete else None,
            "available_at": min(day + PUBLICATION_LAG, retrieved).isoformat(),
            "complete": complete,
            "source": SOURCE,
            "retrieved_at": retrieved.isoformat(),
        })
    if not rows:
        raise ValueError("SoSoValue returned no ETF rows")
    return pd.DataFrame(rows, columns=COLUMNS)


def merge(existing, new):
    """Add new rows only for dates that have no complete row yet; never rewrite existing rows."""
    if existing is None or existing.empty:
        return new.sort_values(["date", "available_at"]).reset_index(drop=True)
    existing = existing.copy()
    for column in COLUMNS:
        if column not in existing:
            existing[column] = None
    done = set(existing.loc[existing.complete.astype(str).str.lower() == "true", "date"].astype(str))
    seen = set(zip(existing.date.astype(str), existing.source.astype(str)))
    keep = new[[d not in done and ((d, s) not in seen or c) for d, s, c in
                zip(new.date, new.source, new.complete)]]
    # an incomplete SoSoValue row is replaced by its later complete version
    stale = existing.source.eq(SOURCE) & existing.date.isin(keep.loc[keep.complete, "date"]) \
        & (existing.complete.astype(str).str.lower() != "true")
    merged = pd.concat([existing[~stale], keep], ignore_index=True)
    return merged.sort_values(["date", "available_at"]).reset_index(drop=True)


def update_csv(path, key, *, get=None, now=None):
    path = Path(path)
    existing = pd.read_csv(path) if path.is_file() else None
    merged = merge(existing, fetch(key, get=get, now=now))
    added = len(merged) - (0 if existing is None else len(existing))
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False, na_rep="")
    return merged, added


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--csv", default="data/etf_flows.csv")
    args = parser.parse_args(argv)
    merged, added = update_csv(args.csv, api_key(args.env_file))
    print(f"{len(merged)} ETF rows ({added:+d}) {merged.date.min()}..{merged.date.max()} -> {args.csv}")


if __name__ == "__main__":
    main()

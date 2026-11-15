"""Validate and normalize a sourced, timestamped ETF-flow CSV for the runner.

This imports reviewed data; it does not scrape an evolving/preliminary table
or infer net fund flows from ETF prices, trading volume or AUM changes.
"""

import argparse
import math
from pathlib import Path

import pandas as pd


def import_flows(source, output="data/etf_flows.csv", *, units="usd"):
    if units not in {"usd", "usd_millions"}:
        raise ValueError("units must be usd or usd_millions")
    frame = pd.read_csv(source)
    columns = {"date", "net_flow_usd", "available_at", "complete", "source"}
    if not columns <= set(frame) or frame.empty:
        raise ValueError("need date, net_flow_usd, available_at, complete and source columns")
    dates = pd.to_datetime(frame.date, errors="raise", utc=True)
    releases = []
    for raw in frame.available_at:
        stamp = pd.Timestamp(raw)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("available_at needs a timezone")
        releases.append(stamp.tz_convert("UTC"))
    if any(a < d for a, d in zip(releases, dates)):
        raise ValueError("ETF data cannot be available before its reporting date")
    if not frame.complete.map(lambda x: str(x).lower() in {"true", "false"}).all():
        raise ValueError("complete must be true or false")
    frame["complete"] = frame.complete.map(lambda x: str(x).lower() == "true")
    flow = pd.to_numeric(frame.net_flow_usd, errors="raise")
    if not all(math.isfinite(float(x)) for x in flow[frame.complete]):
        raise ValueError("complete ETF reports need finite net flows")
    if not frame.source.map(lambda x: isinstance(x, str) and bool(x.strip())).all():
        raise ValueError("a named source is required")
    frame["date"] = dates.dt.strftime("%Y-%m-%d")
    frame["available_at"] = [x.isoformat() for x in releases]
    if frame.duplicated(["date", "available_at"]).any():
        raise ValueError("duplicate ETF vintage")
    frame["net_flow_usd"] = flow * (1_000_000 if units == "usd_millions" else 1)
    frame = frame.sort_values(["date", "available_at"])
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False, na_rep="")
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", default="data/etf_flows.csv")
    parser.add_argument("--units", choices=["usd", "usd_millions"], default="usd")
    args = parser.parse_args()
    frame = import_flows(args.input, args.output, units=args.units)
    print(f"Imported {len(frame)} ETF report vintages into {args.output}")


if __name__ == "__main__":
    main()

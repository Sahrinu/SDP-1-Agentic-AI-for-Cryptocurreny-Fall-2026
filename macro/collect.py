"""Download completed Yahoo daily closes and produce data/macro.csv.

python -m macro.collect --start 2026-08-01 --end 2026-11-13
The end date is inclusive. Source history includes a 30-day warmup.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SYMBOLS = {"dxy": "DX-Y.NYB", "treasury_10y": "^TNX"}
MAX_AGE_DAYS = 4


def align_daily(series, start, end):
    """Forward fill from completed source sessions, never backfill.

    This historical approximation assumes a session close is available at the
    following UTC midnight. It does not establish actual first-publication time.
    Retain original dates so weekends cannot disguise stale data as fresh data.
    """
    calendar = pd.date_range(start, end, freq="D", tz="UTC")
    if calendar.empty:
        raise ValueError("end must be on or after start")
    out = pd.DataFrame(index=calendar)
    for field in SYMBOLS:
        s = series[field].copy()
        # Preserve the exchange's labelled calendar date, not the UTC conversion
        # of a daily bar's midnight timestamp.
        s.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in s.index], tz="UTC")
        s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
        if s.empty or s.index.has_duplicates or (s <= 0).any():
            raise ValueError(f"empty, duplicate, or non-positive {field} history")
        source = pd.DataFrame({field: s, field + "_observed_on": s.index})
        # Explicit availability, separate from the observation date.
        source[field + "_available_at"] = source.index + pd.Timedelta(days=1)
        decisions = pd.DataFrame({"cutoff": calendar + pd.Timedelta(days=1)})
        aligned = pd.merge_asof(
            decisions, source.reset_index(drop=True), left_on="cutoff",
            right_on=field + "_available_at", direction="backward",
        )
        aligned.index = calendar
        age = (pd.Series(calendar, index=calendar) - aligned[field + "_observed_on"]).dt.days
        aligned.loc[age > MAX_AGE_DAYS, field] = float("nan")
        out[field] = aligned[field]
        out[field + "_observed_on"] = aligned[field + "_observed_on"].dt.strftime("%Y-%m-%d")
        out[field + "_available_at"] = aligned[field + "_available_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        out[field + "_forward_filled"] = age.gt(0) & out[field].notna()
    out["source"] = "Yahoo Finance: DX-Y.NYB and ^TNX"
    out["availability_basis"] = "assumed_next_UTC_midnight_not_verified_vintage"
    out.index.name = "date"
    return out


def collect(start, end, *, output="data/macro.csv", downloader=None, now=None):
    now = pd.Timestamp(now if now is not None else datetime.now(timezone.utc))
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    now = now.tz_convert("UTC")
    start, end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    if end < start or end >= now.tz_convert("UTC").date():
        raise ValueError("require start <= end and end before the current UTC day")
    if downloader is None:
        import yfinance as yf  # Optional network dependency; forecast never downloads.
        downloader = yf.download
    series, raw = {}, {}
    warmup = start - pd.Timedelta(days=30)
    for field, symbol in SYMBOLS.items():
        frame = downloader(symbol, start=str(warmup), end=str(end + pd.Timedelta(days=1)),
                           interval="1d", auto_adjust=False, progress=False,
                           threads=False, multi_level_index=False, timeout=20)
        if frame is None or frame.empty or "Close" not in frame:
            raise ValueError(f"Yahoo returned no usable closes for {symbol}")
        if not isinstance(frame["Close"], pd.Series):
            raise ValueError(f"unexpected Yahoo column layout for {symbol}")
        series[field] = frame["Close"]
        raw[field] = [{"date": str(pd.Timestamp(i).date()), "close": float(v)}
                      for i, v in frame["Close"].dropna().items()]
    daily = align_daily(series, start, end)
    # Validate both downloads before writing anything; never substitute fake data.
    if daily[list(SYMBOLS)].notna().sum().min() == 0:
        raise ValueError("no usable macro observations in the requested period")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / "macro_raw"
    archive.mkdir(exist_ok=True)
    snapshot = archive / ("yahoo_" + now.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    snapshot.write_text(json.dumps({
        "retrieved_at": now.isoformat(), "symbols": SYMBOLS,
        "availability_basis": "assumed_next_UTC_midnight_not_verified_vintage",
        "yield_units": "percent as quoted by Yahoo ^TNX; no divide-by-ten conversion",
        "observations": raw,
    }, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    daily.to_csv(destination, date_format="%Y-%m-%d", na_rep="")
    return daily


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--end", default=str(datetime.now(timezone.utc).date() - pd.Timedelta(days=1)))
    parser.add_argument("--output", default="data/macro.csv")
    args = parser.parse_args()
    daily = collect(args.start, args.end, output=args.output)
    print(f"Saved {len(daily)} daily rows to {args.output}; raw snapshot beside CSV.")


if __name__ == "__main__":
    main()

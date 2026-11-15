"""Read-only Binance BTCUSDT daily funding/OI collector; no API key or orders."""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

BASE = "https://fapi.binance.com"


def request_json(path, params):
    request = Request(BASE + path + "?" + urlencode(params), headers={"User-Agent": "SDP-research/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("Binance returned an unexpected response")
    return payload


def normalize(funding, interest, start, end, *, retrieved_at):
    captured = pd.Timestamp(retrieved_at)
    if captured.tzinfo is None:
        raise ValueError("retrieved_at requires a timezone")
    captured = captured.tz_convert("UTC")
    fund = pd.DataFrame(funding)
    oi = pd.DataFrame(interest)
    if fund.empty or oi.empty:
        raise ValueError("no funding or open-interest data; no fake fallback")
    if set(fund.symbol) != {"BTCUSDT"} or set(oi.symbol) != {"BTCUSDT"}:
        raise ValueError("collector supports BTCUSDT only")
    if fund.fundingTime.duplicated().any() or oi.timestamp.duplicated().any():
        raise ValueError("duplicate provider timestamps")
    fund["at"] = pd.to_datetime(fund.fundingTime, unit="ms", utc=True)
    oi["at"] = pd.to_datetime(oi.timestamp, unit="ms", utc=True)
    rows = []
    for day in pd.date_range(start, end, tz="UTC"):
        close = day + pd.Timedelta(days=1)
        values = fund[(fund["at"] >= day) & (fund["at"] < close)].sort_values("at")
        point = oi[(oi["at"] <= close) & (oi["at"] >= close - pd.Timedelta(hours=1))].sort_values("at").tail(1)
        expected = [day + pd.Timedelta(hours=h) for h in (0, 8, 16)]
        # Provider funding timestamps may be milliseconds after a scheduled slot.
        # Require exactly one settlement in each 00/08/16 UTC slot with a bounded
        # one-second delay; retain the untouched timestamps in the raw archive.
        slots = list(values["at"].dt.floor("8h"))
        offsets = values["at"] - values["at"].dt.floor("8h")
        aligned = bool(((offsets >= pd.Timedelta(0)) & (offsets <= pd.Timedelta(seconds=1))).all())
        complete = slots == expected and aligned and len(point) == 1 and close <= captured
        rows.append({
            "date": str(day.date()),
            "funding_rate": float(pd.to_numeric(values.fundingRate).mean()) if len(values) else None,
            "open_interest_usd": float(point.iloc[0].sumOpenInterestValue) if len(point) else None,
            "open_interest_observed_at": point.iloc[0]["at"].isoformat() if len(point) else None,
            # Collection time is a conservative availability bound. Downloading
            # history today does not prove when each value was first published.
            "available_at": max(captured, close).isoformat(),
            "complete": complete, "source": "Binance USD-M BTCUSDT; OI value in USDT as USD proxy",
        })
    frame = pd.DataFrame(rows)
    ready = frame[frame.complete]
    if (not all(math.isfinite(float(x)) and abs(x) <= 0.05 for x in ready.funding_rate)
            or not all(math.isfinite(float(x)) and x > 0 for x in ready.open_interest_usd)):
        raise ValueError("provider returned invalid funding or OI values")
    return frame


def collect(start, end, *, output="data/borrowing.csv", transport=None, now=None):
    explicit_clock = now is not None
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        raise ValueError("now needs a timezone")
    now = now.tz_convert("UTC")
    first, last = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    if first > last or last >= now.normalize() or first < now.normalize() - pd.Timedelta(days=25):
        raise ValueError("use completed dates within the last 25 days; older OI needs an existing archive")
    send = transport or request_json
    params = {"symbol": "BTCUSDT", "startTime": int(first.timestamp() * 1000),
              "endTime": int((last + pd.Timedelta(days=1)).timestamp() * 1000), "limit": 1000}
    funding = send("/fapi/v1/fundingRate", params)
    oi_params = {**params, "period": "1d", "limit": 500}
    interest = send("/futures/data/openInterestHist", oi_params)
    # Capture after both responses; never claim the request start as receipt time.
    captured = now if explicit_clock else pd.Timestamp.now(tz="UTC")
    frame = normalize(funding, interest, start, end, retrieved_at=captured)
    if not frame.complete.any():
        raise ValueError("no complete funding/OI days returned; nothing written")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.parent / "borrowing_raw"
    raw.mkdir(exist_ok=True)
    (raw / (captured.strftime("%Y%m%dT%H%M%S%fZ") + ".json")).write_text(
        json.dumps({"retrieved_at": captured.isoformat(), "funding": funding, "open_interest": interest},
                   indent=2, allow_nan=False) + "\n", encoding="utf-8")
    frame.to_csv(destination, index=False, na_rep="")
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", default="data/borrowing.csv")
    args = parser.parse_args()
    try:
        frame = collect(args.start, args.end, output=args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Collection failed ({type(exc).__name__}); no replacement data written. Check provider access, dates and trusted TLS certificates.\n")
    print(f"Saved {len(frame)} rows, {int(frame.complete.sum())} complete. Receipt timestamps retained.")


if __name__ == "__main__":
    main()

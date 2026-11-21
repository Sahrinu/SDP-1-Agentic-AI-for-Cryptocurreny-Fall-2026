"""Multi-year daily stablecoin supply (DefiLlama) and BTC-USD (Coinbase) history.

Used to train the liquidity model across regimes (2019 onward). CoinGecko stays
the source for live quotes, freshness checks, volume and the peg-stress model.

- DefiLlama ``stablecoincharts/all?stablecoin=<id>``: daily on-chain circulating
  supply (excluding unreleased treasury tokens) and its USD value; the implied
  price is USD value / circulating. Free, no key.
- Coinbase Exchange daily candles: BTC-USD open price at 00:00 UTC, matching the
  00:00 UTC DefiLlama snapshot so no feature uses information after its date.

CLI:  python -m market_data.stablecoin.defillama [--output data/stablecoin_history_long.csv]
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

LLAMA_IDS = {"usdt": 1, "usdc": 2, "dai": 5}
START = datetime(2019, 11, 19, tzinfo=timezone.utc)  # first day all three coins exist
LLAMA_URL = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={}"
COINBASE_URL = ("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"
                "&start={}&end={}")
# Exact protocol supply at halving heights 630,000 and 840,000 (BTC).
HALVINGS = ((pd.Timestamp("2020-05-11", tz="UTC"), 18_375_000.0),
            (pd.Timestamp("2024-04-20", tz="UTC"), 19_687_500.0))


def _get(url, headers=None):
    from market_data.http import get_json
    return get_json(url, timeout=30, headers=headers or {})


def btc_supply(index):
    """Approximate circulating BTC: exact at halvings, linear between, schedule rate outside."""
    (t0, s0), (t1, s1) = HALVINGS
    days = lambda a, b: (a - b) / pd.Timedelta(days=1)
    out = []
    for t in pd.DatetimeIndex(index):
        if t < t0:
            out.append(s0 - 900 * days(t0, t))          # 6.25 BTC x 144 blocks/day
        elif t <= t1:
            out.append(s0 + (s1 - s0) * days(t, t0) / days(t1, t0))
        else:
            out.append(s1 + 450 * days(t, t1))          # 3.125 BTC x 144 blocks/day
    return pd.Series(out, index=index)


def parse_llama(rows):
    """(implied price, USD value) daily series from a stablecoincharts payload."""
    index = pd.to_datetime([int(r["date"]) for r in rows], unit="s", utc=True).normalize()
    circ = pd.Series([float(r["totalCirculating"]["peggedUSD"]) for r in rows], index)
    usd = pd.Series([float(r["totalCirculatingUSD"]["peggedUSD"]) for r in rows], index)
    keep = ~index.duplicated(keep="last")
    circ, usd = circ[keep], usd[keep]
    return usd / circ.replace(0, np.nan), usd


def fetch_btc(start=START, end=None, get=_get, pause=0.3):
    end = end or datetime.now(timezone.utc)
    opens, cursor = {}, start
    while cursor < end:
        stop = min(cursor + timedelta(days=299), end)   # Coinbase caps 300 candles per call
        rows = get(COINBASE_URL.format(cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       stop.strftime("%Y-%m-%dT%H:%M:%SZ")))
        for stamp, _low, _high, open_, _close, _vol in rows:
            opens[pd.Timestamp(int(stamp), unit="s", tz="UTC")] = float(open_)
        cursor = stop + timedelta(days=1)
        if pause:
            time.sleep(pause)
    return pd.Series(opens).sort_index()


def fetch_history(get=_get, pause=0.3):
    columns = {}
    for symbol, llama_id in LLAMA_IDS.items():
        price, usd = parse_llama(get(LLAMA_URL.format(llama_id)))
        columns[f"{symbol}_price"], columns[f"{symbol}_cap"] = price, usd
    columns["btc_price"] = fetch_btc(get=get, pause=pause)
    return build_frame(columns)


def build_frame(columns):
    frame = pd.DataFrame(columns).sort_index()
    frame = frame[frame.index >= pd.Timestamp(START)]
    frame["btc_cap"] = frame["btc_price"] * btc_supply(frame.index)
    prices = frame[[f"{s}_price" for s in LLAMA_IDS]]
    frame = frame[(prices > 0.5).all(axis=1) & (prices < 1.5).all(axis=1)].dropna()
    frame.index.name = "date"
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default="data/stablecoin_history_long.csv")
    args = parser.parse_args(argv)
    frame = fetch_history()
    frame.to_csv(args.output)
    print(f"{len(frame)} days {frame.index[0].date()}..{frame.index[-1].date()} -> {args.output}")


if __name__ == "__main__":
    main()

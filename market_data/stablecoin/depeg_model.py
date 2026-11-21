"""Learned next-day stablecoin depeg-risk model from daily CoinGecko history.

Method adapted (reimplemented, no code copied) from the DSE4101 Stablecoin
Depegging Prediction Project: dynamic per-coin peg bands as labels, technical
features on stablecoin prices plus Fear & Greed, chronological out-of-sample
evaluation, and imbalance handling on the training split only (class weights
here instead of SMOTE).  Pure numpy logistic regression; no new dependencies.

CLI:  python -m market_data.stablecoin.depeg_model --env-file .env
"""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .data import COINS

SYMBOLS = [s.lower() for s in COINS.values()]
BAND_FLOOR = 0.001     # 10bp floor: past year had no true depeg, so target is "peg stress"
BAND_SIGMAS = 3.0      # dynamic band = max(floor, 3 x rolling 30d deviation std)
WINDOW = 30


def _get(url, headers=None, timeout=20):
    with urlopen(Request(url, headers={"Accept": "application/json", **(headers or {})}),
                 timeout=timeout) as response:
        return json.load(response)


def fetch_history(api_key, days=365, get=_get):
    """Daily price, market cap and volume per coin, plus Fear & Greed."""
    columns = {}
    for coin_id, symbol in {**COINS, "bitcoin": "BTC"}.items():
        raw = get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
                  f"?vs_currency=usd&days={days}&interval=daily",
                  {"x-cg-demo-api-key": api_key})
        for field, name in (("prices", "price"), ("market_caps", "cap"), ("total_volumes", "vol")):
            series = pd.Series({pd.Timestamp(ms, unit="ms", tz="UTC").normalize(): float(v)
                                for ms, v in raw[field] if v is not None})
            columns[f"{symbol.lower()}_{name}"] = series[~series.index.duplicated(keep="last")]
    frame = pd.DataFrame(columns).sort_index()
    try:
        fng = get(f"https://api.alternative.me/fng/?limit={days + 40}")["data"]
        frame["fng"] = pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC").normalize():
                                  float(r["value"]) for r in fng})
    except Exception:
        frame["fng"] = np.nan
    frame.index.name = "date"
    return frame.dropna(subset=[f"{s}_price" for s in SYMBOLS])


def _rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def build_features(frame):
    """Features at date t use only data up to and including t."""
    f = pd.DataFrame(index=frame.index)
    devs, supply = [], 0
    for s in SYMBOLS:
        dev = frame[f"{s}_price"] - 1
        std = dev.rolling(WINDOW, min_periods=10).std()
        f[f"{s}_absdev_bps"] = dev.abs() * 1e4
        f[f"{s}_band_z"] = dev.abs() / std.replace(0, np.nan)          # Bollinger-style
        f[f"{s}_rsi"] = _rsi(frame[f"{s}_price"]).fillna(50)
        f[f"{s}_cap_chg7"] = frame[f"{s}_cap"].pct_change(7) * 100
        f[f"{s}_turnover"] = frame[f"{s}_vol"] / frame[f"{s}_cap"]
        devs.append(dev.abs())
        supply = supply + frame[f"{s}_cap"] / frame[f"{s}_price"]
    f["max_absdev_bps"] = pd.concat(devs, axis=1).max(axis=1) * 1e4
    f["supply_chg1"] = supply.pct_change(1) * 100
    f["supply_chg7"] = supply.pct_change(7) * 100
    fng = frame["fng"].ffill()
    f["fng"] = fng.fillna(50)
    f["fng_chg7"] = fng.diff(7).fillna(0)
    return f.replace([np.inf, -np.inf], np.nan)


def build_labels(frame):
    """1 if any coin breaches its dynamic band on the NEXT day (band set from data to t)."""
    hits = []
    for s in SYMBOLS:
        dev = frame[f"{s}_price"] - 1
        band = (BAND_SIGMAS * dev.rolling(WINDOW, min_periods=10).std()).clip(lower=BAND_FLOOR)
        hits.append(dev.shift(-1).abs() > band)
    label = pd.concat(hits, axis=1).any(axis=1).astype(float)
    label.iloc[-1] = np.nan  # tomorrow unknown
    return label


def _fit(x, y, l2=1.0, steps=3000, lr=0.1):
    pos = max(y.mean(), 1e-3)
    weight = np.where(y == 1, 0.5 / pos, 0.5 / (1 - pos))  # balanced class weights
    w, b = np.zeros(x.shape[1]), 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(x @ w + b)))
        g = weight * (p - y)
        w -= lr * (x.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def _auc(y, p):
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    n1, n0 = y.sum(), len(y) - y.sum()
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else None


def train(frame, test_fraction=0.25):
    features, labels = build_features(frame), build_labels(frame)
    table = features.assign(y=labels).dropna()
    names = list(features.columns)
    split = int(len(table) * (1 - test_fraction))
    tr, te = table.iloc[:split], table.iloc[split:]

    def scaled(part, mean, std):
        return ((part[names] - mean) / std).to_numpy()

    mean, std = tr[names].mean(), tr[names].std().replace(0, 1)
    w, b = _fit(scaled(tr, mean, std), tr.y.to_numpy())
    p = 1 / (1 + np.exp(-(scaled(te, mean, std) @ w + b)))
    y = te.y.to_numpy()
    base = float(tr.y.mean())
    metrics = {
        "test_start": str(te.index[0].date()), "test_end": str(te.index[-1].date()),
        "test_rows": len(te), "test_depeg_days": int(y.sum()),
        "auc": _auc(y, p),
        "precision_at_0.5": float(y[p >= .5].mean()) if (p >= .5).any() else None,
        "recall_at_0.5": float((p[y == 1] >= .5).mean()) if y.sum() else None,
    }
    # Final model refit on all labelled rows; probabilities re-centred to base rate.
    mean, std = table[names].mean(), table[names].std().replace(0, 1)
    w, b = _fit(scaled(table, mean, std), table.y.to_numpy())
    raw_mean = float((1 / (1 + np.exp(-(scaled(table, mean, std) @ w + b)))).mean())
    return {
        "version": 1, "features": names, "mean": mean.tolist(), "std": std.tolist(),
        "weights": w.tolist(), "bias": float(b), "base_rate": float(table.y.mean()),
        "train_base_rate": base, "raw_mean": raw_mean,
        "train_start": str(table.index[0].date()), "train_end": str(table.index[-1].date()),
        "trained_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics,
        "band": {"floor": BAND_FLOOR, "sigmas": BAND_SIGMAS, "window": WINDOW},
    }


def predict(model, frame):
    """Depeg risk for the last row of `frame`, calibrated so the average equals the base rate."""
    row = build_features(frame).iloc[-1]
    x = ((row[model["features"]] - pd.Series(model["mean"], model["features"]))
         / pd.Series(model["std"], model["features"])).fillna(0).clip(-6, 6).to_numpy()
    raw = 1 / (1 + math.exp(-(float(x @ np.array(model["weights"])) + model["bias"])))
    # Balanced weights inflate probabilities; map raw odds back to the true base rate.
    odds = raw / (1 - raw) * (model["base_rate"] / (1 - model["base_rate"])) \
        / (model["raw_mean"] / (1 - model["raw_mean"]))
    contrib = dict(zip(model["features"], x * np.array(model["weights"])))
    top = sorted(contrib, key=lambda k: -abs(contrib[k]))[:3]
    return odds / (1 + odds), row, top


def save_history(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path)


def load_history(path):
    frame = pd.read_csv(path, index_col="date")
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--history", default="data/stablecoin_history.csv")
    parser.add_argument("--model", default="data/stablecoin_depeg_model.json")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args(argv)
    from .data import CoinGeckoClient
    key = CoinGeckoClient(env_file=args.env_file).api_key
    if not key:
        raise SystemExit("COINGECKO_DEMO_API_KEY not configured")
    frame = fetch_history(key, args.days)
    save_history(frame, args.history)
    model = train(frame)
    Path(args.model).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: model[k] for k in ("train_start", "train_end", "base_rate", "metrics")}, indent=2))


if __name__ == "__main__":
    main()

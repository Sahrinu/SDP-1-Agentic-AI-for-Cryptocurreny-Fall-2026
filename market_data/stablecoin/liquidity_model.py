"""Stablecoin issuance-asymmetry liquidity signal for Bitcoin direction.

Trained on multi-year DefiLlama supply + Coinbase BTC-USD history (2019 onward).

Evidence (2020-2026, Spearman rank IC vs forward 7-day BTC log return):
- A one-year sample (2026) suggested raw supply growth and the USDT premium
  predict 7-day returns (IC ~0.2-0.3). Over 2020-2026 that effect is
  regime-dependent and mostly absent (positive in only 3 of 7 years).
- Issuance follows prices (Kristoufek 2021): past 7d BTC return predicts
  next-day supply growth (IC ~0.23).
- The consistently signed effect is the asymmetry documented by Griffin &
  Shams (2020, J. Finance) and Ante, Fiedler & Strehle (2021, FRL): issuance
  not explained by past BTC returns ("abnormal issuance", positive in 6/7
  years) and minting after a BTC dip (5/7 years). It is small (IC ~0.03-0.07).
- Walk-forward by calendar year: rank IC positive in every test year, but
  pooled Brier is about equal to a 0.5 coin flip.  The fitted slope is
  therefore small and the agent stays near 0.5 unless these signals are large.

Component SIGNS are fixed from the literature; components are z-scored against
their own trailing 90-day history (point-in-time) and averaged. Only the
score -> probability mapping is fitted.

CLI:  python -m market_data.stablecoin.liquidity_model
"""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .depeg_model import SYMBOLS, load_history

ZWINDOW = 90
REGRESSION_WINDOW = 90
COMPONENTS = {               # name: sign
    "abnormal_issuance": +1,   # supply growth unexplained by past BTC returns
    "mint_after_dip": +1,      # supply growth while BTC fell over the past week
    "usdt_premium_ma3": +1,    # paying above $1 to enter crypto
    "dominance_z30": -1,       # rotation into stablecoins = risk-off
}
HORIZONS = (1, 7)


def abnormal_issuance(supply_growth, past7, past14, window=REGRESSION_WINDOW):
    """Residual of 7d supply growth on past BTC returns, from a trailing regression (point-in-time)."""
    y = supply_growth.to_numpy(float)
    x = np.c_[np.ones(len(y)), past7.to_numpy(float), past14.to_numpy(float)]
    out = np.full(len(y), np.nan)
    for i in range(window, len(y)):
        yy, xx = y[i - window:i + 1], x[i - window:i + 1]
        ok = np.isfinite(yy) & np.isfinite(xx).all(axis=1)
        if ok.sum() < 60 or not np.isfinite(y[i]) or not np.isfinite(x[i]).all():
            continue
        beta = np.linalg.lstsq(xx[ok], yy[ok], rcond=None)[0]
        out[i] = y[i] - x[i] @ beta
    return pd.Series(out, index=supply_growth.index)


def components(frame):
    supply = sum(frame[f"{s}_cap"] / frame[f"{s}_price"] for s in SYMBOLS)
    cap = sum(frame[f"{s}_cap"] for s in SYMBOLS)
    dominance = cap / (cap + frame["btc_cap"])
    log_price = np.log(frame["btc_price"])
    past7, past14 = log_price - log_price.shift(7), log_price - log_price.shift(14)
    growth = supply.pct_change(7) * 100
    c = pd.DataFrame(index=frame.index)
    c["abnormal_issuance"] = abnormal_issuance(growth, past7, past14)
    c["mint_after_dip"] = growth.where(past7 < 0, 0.0).where(past7.notna())
    c["usdt_premium_ma3"] = ((frame.usdt_price - 1) * 1e4).rolling(3).mean()
    c["dominance_z30"] = (dominance - dominance.rolling(30).mean()) / dominance.rolling(30).std()
    return c[list(COMPONENTS)]


def score(frame):
    """Composite z-score per day; each component scaled by its trailing history only."""
    c = components(frame)
    z = (c - c.rolling(ZWINDOW, min_periods=45).mean()) / c.rolling(ZWINDOW, min_periods=45).std()
    signed = z.clip(-3, 3).mul(pd.Series(COMPONENTS))
    return signed.mean(axis=1, skipna=False), signed


def _logistic(x, y, l2=1.0, steps=4000, lr=0.2):
    a = b = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(a + b * x)))
        a -= lr * (p - y).mean()
        b -= lr * (((p - y) * x).mean() + l2 * b / len(y))
    return a, b


def targets(frame, horizon):
    log_price = np.log(frame["btc_price"])
    up = (log_price.shift(-horizon) > log_price).astype(float)
    up[log_price.shift(-horizon).isna()] = np.nan
    return up


def _table(s, frame, horizon):
    return pd.concat([s.rename("s"), targets(frame, horizon).rename("y")], axis=1).dropna()


def _fit_table(frame, end=None, s=None):
    s = score(frame)[0] if s is None else s
    fits = {}
    for h in HORIZONS:
        t = _table(s, frame, h)
        if end is not None:  # labels must be fully known before `end`
            t = t[t.index + pd.Timedelta(days=h) < end]
        if len(t) < 30:
            raise ValueError("Too little history to fit the liquidity model")
        a, b = _logistic(t.s.to_numpy(), t.y.to_numpy())
        fits[str(h)] = {"a": float(a), "b": float(b), "rows": len(t),
                        "train_ic": float(t.corr(method="spearman").iloc[0, 1])}
    return fits


def walk_forward(frame, min_train_days=365):
    """Expanding window: refit on all prior data each 1 January, test that calendar year."""
    s, _ = score(frame)
    first = s.first_valid_index()
    results = {f"h{h}": [] for h in HORIZONS}
    if first is None:
        return results
    for year in sorted(set(frame.index.year)):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        stop = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        if (start - first).days < min_train_days:
            continue
        fits = _fit_table(frame, end=start, s=s)
        for h in HORIZONS:
            t = _table(s, frame, h)
            t = t[(t.index >= start) & (t.index < stop)]
            if len(t) < 60:
                continue
            f = fits[str(h)]
            p = (1 / (1 + np.exp(-(f["a"] + f["b"] * t.s)))).to_numpy()
            y = t.y.to_numpy()
            log_price = np.log(frame["btc_price"])
            momentum_up = ((log_price - log_price.shift(h)).reindex(t.index) > 0).to_numpy()
            results[f"h{h}"].append({
                "year": year, "rows": len(t), "btc_up_rate": float(y.mean()),
                "ic": float(t.corr(method="spearman").iloc[0, 1]),
                "brier": float(((p - y) ** 2).mean()), "brier_coinflip": 0.25,
                "hit_rate": float(((p > 0.5) == (y == 1)).mean()),
                "hit_always_majority": float(max(y.mean(), 1 - y.mean())),
                "hit_momentum": float((momentum_up == (y == 1)).mean()),
            })
    return results


def summarize(runs):
    if not runs:
        return {}
    n = np.array([r["rows"] for r in runs], float)
    return {
        "years": len(runs),
        "pooled_brier": float(np.average([r["brier"] for r in runs], weights=n)),
        "brier_coinflip": 0.25,
        "mean_ic": float(np.mean([r["ic"] for r in runs])),
        "years_ic_positive": int(sum(r["ic"] > 0 for r in runs)),
        "years_beating_coinflip": int(sum(r["brier"] < 0.25 for r in runs)),
    }


def train(frame):
    frame = frame.dropna(subset=["btc_price", "btc_cap"])
    runs = walk_forward(frame)
    return {
        "version": 3, "components": COMPONENTS, "zwindow": ZWINDOW,
        "regression_window": REGRESSION_WINDOW, "source": "DefiLlama supply + Coinbase BTC-USD",
        "fits": _fit_table(frame),
        "train_start": str(frame.index[0].date()), "train_end": str(frame.index[-1].date()),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "walk_forward": runs, "summary": {k: summarize(v) for k, v in runs.items()},
    }


def predict(model, frame):
    """(p_up_1d, p_up_7d, score, per-component signed z) for the last row."""
    s, signed = score(frame)
    value = float(s.iloc[-1])
    if not math.isfinite(value):
        raise ValueError("Insufficient history for liquidity score")
    p = {h: 1 / (1 + math.exp(-(model["fits"][str(h)]["a"] + model["fits"][str(h)]["b"] * value)))
         for h in HORIZONS}
    return p[1], p[7], value, signed.iloc[-1].to_dict()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", default="data/stablecoin_history_long.csv")
    parser.add_argument("--model", default="data/stablecoin_liquidity_model.json")
    args = parser.parse_args(argv)
    model = train(load_history(args.history))
    Path(args.model).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    for h, runs in model["walk_forward"].items():
        for r in runs:
            print(f"{h} {r['year']}: Brier {r['brier']:.3f} (coin flip 0.250), IC {r['ic']:+.2f}, "
                  f"hit {r['hit_rate']:.0%} vs majority {r['hit_always_majority']:.0%} / momentum {r['hit_momentum']:.0%}")
        print(f"{h} summary: {model['summary'][h]}")


if __name__ == "__main__":
    main()

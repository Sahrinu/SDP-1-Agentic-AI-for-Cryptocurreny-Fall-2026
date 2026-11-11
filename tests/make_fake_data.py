# Makes a fake Bitcoin price file so the engine and run script can be tested
# before the real data and agents are ready.
#   python3 tests/make_fake_data.py

from pathlib import Path

import numpy as np
import pandas as pd

out = Path(__file__).resolve().parent / "fake_data"
out.mkdir(exist_ok=True)

rng = np.random.default_rng(7)
dates = pd.date_range("2026-06-01", "2026-11-01", freq="D")
returns = rng.normal(0.0005, 0.025, len(dates))
close = 60000 * np.exp(np.cumsum(returns))

pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": close.round(2)}).to_csv(
    out / "btc_prices.csv", index=False)
print(f"wrote {out / 'btc_prices.csv'} ({len(dates)} days)")

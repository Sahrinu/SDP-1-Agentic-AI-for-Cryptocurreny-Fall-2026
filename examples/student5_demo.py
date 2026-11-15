"""Offline Student 5 demonstration. Every market value here is synthetic."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.world_economy import forecast
from market_data.macro import align_daily
from risk.gateway import execute_guarded
from execution.paper_trading import PaperTrader


def main():
    dates = pd.bdate_range("2026-10-19", "2026-11-13")
    macro = align_daily({
        "dxy": pd.Series([102 - i * 0.1 for i in range(len(dates))], index=dates),
        "treasury_10y": pd.Series([4.3 - i * 0.01 for i in range(len(dates))], index=dates),
    }, "2026-10-19", "2026-11-15")
    result = {"synthetic": True, "forecast": forecast("2026-11-15", {"macro": macro}),
              "weekend": macro.tail(3).reset_index().astype(str).to_dict("records"), "risk_checks": []}
    trader = PaperTrader(initial_cash="10000")
    for day, price, action in [(16, "50000", "BUY"), (17, "50000", "BUY"),
                                (18, "80000", "HOLD")]:
        approval, trade = execute_guarded(
            trader, action, price, timestamp=datetime(2026, 11, day, tzinfo=timezone.utc),
        )
        result["risk_checks"].append({"requested": action, "approval": approval.to_dict(),
                                      "traded": trade is not None, "account": trader.summary(price)})
    out = Path(__file__).resolve().parents[1] / "outputs" / "student5_demo.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Saved {out}")


if __name__ == "__main__":
    main()

"""Exercise the four new agents and paper trading with clearly synthetic data."""

import json
import sys
from datetime import datetime, timezone

import pandas as pd

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import borrowing, etf, crash, contrarian
from experiments.run import ROOT, run, load_data, data_upto
from experiments.paper import TradingPolicy


def main():
    folder = ROOT / "outputs" / ("new_agents_demo_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    data_dir, agents_dir = folder / "data", folder / "agents"
    data_dir.mkdir(parents=True)
    agents_dir.mkdir()
    dates = pd.date_range("2026-08-01", "2026-11-16")
    closes = [20000 * 1.001 ** i if i < len(dates) - 12
              else 22000 * .975 ** (i - len(dates) + 12) for i in range(len(dates))]
    pd.DataFrame({"date": dates, "open": [closes[0], *closes[:-1]], "close": closes}).to_csv(data_dir / "btc_prices.csv", index=False)
    debt = pd.DataFrame({"date": dates, "funding_rate": [-.0005 if d < pd.Timestamp("2026-11-05") else .0005 for d in dates],
                         "open_interest_usd": [1e9 * 1.02 ** i for i in range(len(dates))],
                         "available_at": [(d + pd.Timedelta(hours=23)).tz_localize("UTC").isoformat() for d in dates],
                         "complete": True, "source": "SYNTHETIC demo funding and OI"})
    debt.to_csv(data_dir / "borrowing.csv", index=False)
    sessions = pd.bdate_range("2026-08-01", "2026-11-16")
    pd.DataFrame({"date": sessions, "net_flow_usd": [2e9 if d < pd.Timestamp("2026-11-05") else -2e9 for d in sessions],
                  "available_at": [(d + pd.Timedelta(days=1, hours=2)).tz_localize("UTC").isoformat() for d in sessions],
                  "complete": True, "source": "SYNTHETIC demo ETF flow reports"}).to_csv(data_dir / "etf_flows.csv", index=False)
    for name in ("borrowing", "etf", "crash", "contrarian"):
        (agents_dir / (name + ".py")).write_text(f"from agents.{name} import *\n", encoding="utf-8")
    print("SYNTHETIC INPUTS: real new-agent code, fabricated price/funding/flow observations.")
    run("2026-11-02", "2026-11-15", 0, data_dir, agents_dir, folder / "results",
        paper_policy=TradingPolicy(), synthetic=True)
    day = "2026-11-15"
    view = data_upto(load_data(data_dir), pd.Timestamp(day))
    answers = [module.forecast(day, view) for module in (borrowing, etf, crash)]
    view["peer_forecasts"] = {"day": day, "probabilities": {r["agent"]: r["p_up"] for r in answers if not r["data_missing"]}}
    answers.append(contrarian.forecast(day, view))
    (folder / "agent_evidence.json").write_text(json.dumps({"synthetic": True, "agents": answers}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(answers, indent=2))
    print(f"Saved evidence and paper accounts to {folder}")


if __name__ == "__main__":
    main()

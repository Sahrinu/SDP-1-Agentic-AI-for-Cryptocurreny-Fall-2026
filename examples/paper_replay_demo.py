"""One-command offline forecast -> risk -> paper-trade replay, all synthetic."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.paper import TradingPolicy
from experiments.run import ROOT, run


def main():
    out = ROOT / "outputs" / ("paper_demo_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    print("SYNTHETIC DEMO: fake agents and prices; delayed next-close execution.")
    run("2026-10-01", "2026-10-31", 5, ROOT / "tests" / "fake_data",
        ROOT / "tests" / "fake_agents", out,
        paper_policy=TradingPolicy(execution="next_close"), synthetic=True)
    print(f"Paper accounts, risk decisions, equity and comparison summary: {out / 'paper'}")


if __name__ == "__main__":
    main()

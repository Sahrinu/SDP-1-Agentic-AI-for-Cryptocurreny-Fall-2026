"""Student 1 Long-term-agent command using an archived daily CSV."""

import argparse
import json
from datetime import date
from pathlib import Path

from agents.specialists.long_term import analyze_long_term
from market_data.bitcoin.history import load_daily_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore the BTCUSDT long-horizon price trend")
    parser.add_argument("--history", type=Path, default=Path("outputs/btcusdt_daily.csv"))
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="Historical cutoff (YYYY-MM-DD); data after this date is excluded")
    args = parser.parse_args()
    report = analyze_long_term(load_daily_history(args.history), as_of=args.as_of)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()

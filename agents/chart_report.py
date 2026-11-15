"""Student 1 Chart-agent command."""

import argparse
import json
from pathlib import Path

from agents.specialists.chart import analyze_chart
from market_data.spot_config import MarketConfig
from market_data.bitcoin.binance import fetch_completed_candles
from market_data.bitcoin.history import load_daily_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze completed BTCUSDT Spot candles")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--history", type=Path, help="Use saved daily CSV instead of current Binance candles")
    args = parser.parse_args()
    if args.history:
        if args.interval != "1d":
            parser.error("--history requires --interval 1d")
        candles = load_daily_history(args.history)
    else:
        candles = fetch_completed_candles(MarketConfig(interval=args.interval))
    report = analyze_chart(candles, interval=args.interval)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()

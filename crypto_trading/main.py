import argparse
import json

from crypto_trading.agents.market import analyze_market
from crypto_trading.config import MarketConfig
from crypto_trading.data.binance import fetch_completed_candles


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Analyze Binance Spot market candles")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    args = parser.parse_args(argv)
    config = MarketConfig(symbol=args.symbol, interval=args.interval)
    candles = fetch_completed_candles(config)
    report = analyze_market(candles, symbol=config.symbol, interval=config.interval)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()


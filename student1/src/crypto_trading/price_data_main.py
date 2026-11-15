"""Student 1 price-data command: save completed daily BTC/USDT candles."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from crypto_trading.data.bitcoin_prices import fetch_daily_history, save_daily_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Download public Binance BTCUSDT daily Spot candles")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2017, 8, 17))
    parser.add_argument("--end", type=date.fromisoformat,
                        default=datetime.now(UTC).date() - timedelta(days=1))
    parser.add_argument("--output", type=Path, default=Path("outputs/btcusdt_daily.csv"))
    args = parser.parse_args()
    candles = fetch_daily_history(args.start, args.end)
    save_daily_history(candles, args.output)
    print(json.dumps({"symbol": "BTCUSDT", "interval": "1d", "source": "Binance Spot",
                      "first_utc_date": args.start.isoformat(),
                      "last_utc_date": datetime.fromtimestamp(candles[-1].open_time_ms / 1000, UTC).date().isoformat(),
                      "candles": len(candles), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()

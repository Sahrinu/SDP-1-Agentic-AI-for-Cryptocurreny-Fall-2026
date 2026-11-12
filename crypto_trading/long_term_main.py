"""Command-line report for the long-term Bitcoin price agent."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta

from crypto_trading.agents.long_term import analyze_long_term, load_daily_prices_csv


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Fit a point-in-time Bitcoin price power-law trend")
    parser.add_argument("--prices", required=True, help="CSV with date,close daily BTC prices in the chosen quote currency")
    parser.add_argument("--as-of", help="Last completed UTC price date (YYYY-MM-DD); defaults to yesterday")
    parser.add_argument("--quote", choices=("USD", "USDT"), default="USDT", help="Price quote currency")
    parser.add_argument("--horizon-days", type=int, default=365, help="Trend context horizon, at least 30 days")
    args = parser.parse_args(argv)
    cutoff = date.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC).date() - timedelta(days=1)
    prices = load_daily_prices_csv(args.prices)
    report = analyze_long_term(prices, as_of=cutoff, horizon_days=args.horizon_days, quote_currency=args.quote)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()

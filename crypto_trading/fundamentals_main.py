"""Run the current Bitcoin fundamentals evidence report."""

import json
from datetime import UTC, datetime

from crypto_trading.agents.fundamentals import analyze_fundamentals
from crypto_trading.data.coinmetrics import fetch_onchain_days


def main() -> None:
    as_of = datetime.now(UTC)
    days = fetch_onchain_days(as_of=as_of)
    report = analyze_fundamentals(days, as_of=as_of)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()

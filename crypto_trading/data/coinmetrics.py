"""Public daily Bitcoin on-chain metrics from Coin Metrics Community API."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://community-api.coinmetrics.io/v4"
METRICS = ("TxCnt", "AdrActCnt", "HashRate", "FeeTotNtv")


@dataclass(frozen=True)
class OnchainDay:
    day: date
    transactions: int
    active_addresses: int
    hash_rate: Decimal
    fees_btc: Decimal


def parse_onchain_days(rows: list, *, as_of: datetime, lag_days: int = 2) -> list[OnchainDay]:
    """Keep only dates conservatively assumed available by the decision time.

    The provider's historical API does not expose each value's first-publication
    timestamp. A fixed lag is only an approximation for replay, not proof of
    actual historical availability.
    """
    if as_of.tzinfo is None or lag_days < 1:
        raise ValueError("as_of must be timezone-aware and lag_days must be positive")
    cutoff = as_of.astimezone(UTC).date() - timedelta(days=lag_days)
    if not isinstance(rows, list):
        raise ValueError("invalid Coin Metrics response")
    days: list[OnchainDay] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("asset") != "btc":
            raise ValueError("invalid Bitcoin metric row")
        try:
            timestamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            values = [Decimal(str(row[name])) for name in METRICS]
            if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
                raise ValueError("metric time must be UTC")
            if timestamp.time() != datetime.min.time():
                raise ValueError("expected daily metric timestamp")
            if any(not value.is_finite() or value < 0 for value in values):
                raise ValueError("invalid metric value")
            if values[0] != values[0].to_integral_value() or values[1] != values[1].to_integral_value():
                raise ValueError("activity counts must be integers")
        except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
            raise ValueError("invalid Coin Metrics metric row") from exc
        day = timestamp.date()
        if day <= cutoff:
            days.append(OnchainDay(day, int(values[0]), int(values[1]), values[2], values[3]))
    for previous, current in zip(days, days[1:]):
        if current.day <= previous.day:
            raise ValueError("metric dates must be ordered and unique")
    return days


def fetch_onchain_days(*, as_of: datetime | None = None, lag_days: int = 2,
                       history_days: int = 21, timeout_seconds: float = 10.0,
                       base_url: str = BASE_URL) -> list[OnchainDay]:
    """Fetch daily data through a conservative cutoff for a current report."""
    if as_of is None:
        as_of = datetime.now(UTC)
    if as_of.tzinfo is None or lag_days < 1 or history_days < 8 or timeout_seconds <= 0:
        raise ValueError("invalid on-chain request settings")
    if as_of.astimezone(UTC) > datetime.now(UTC):
        raise ValueError("as_of cannot be in the future")
    cutoff = as_of.astimezone(UTC).date() - timedelta(days=lag_days)
    start = cutoff - timedelta(days=history_days - 1)
    query = urlencode({"assets": "btc", "metrics": ",".join(METRICS),
                       "frequency": "1d", "start_time": start.isoformat(),
                       "end_time": cutoff.isoformat(), "page_size": history_days})
    url = f"{base_url.rstrip('/')}/timeseries/asset-metrics?{query}"
    with urlopen(url, timeout=timeout_seconds) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("unexpected Coin Metrics response")
    if payload.get("next_page_url"):
        raise ValueError("incomplete Coin Metrics page")
    return parse_onchain_days(payload["data"], as_of=as_of, lag_days=lag_days)

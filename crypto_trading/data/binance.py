"""Public Binance Spot candles. No API key or account is required."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import urlopen

from crypto_trading.config import MarketConfig


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    close_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_btc: Decimal
    volume_usdt: Decimal
    trade_count: int
    taker_buy_btc: Decimal
    taker_buy_usdt: Decimal

    @property
    def close_time(self) -> str:
        return datetime.fromtimestamp(self.close_time_ms / 1000, UTC).isoformat()


def parse_candles(rows: list, *, as_of_ms: int) -> list[Candle]:
    """Validate and retain only candles whose full interval has ended."""
    result: list[Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 11:
            raise ValueError("invalid Binance candle response")
        try:
            open_ms, close_ms = int(row[0]), int(row[6])
            values = [Decimal(str(row[i])) for i in (1, 2, 3, 4, 5, 7, 9, 10)]
            trades = int(row[8])
        except (ValueError, TypeError, InvalidOperation, IndexError) as exc:
            raise ValueError("invalid Binance candle field") from exc
        op, high, low, close, base_vol, quote_vol, buy_base, buy_quote = values
        if not all(v.is_finite() for v in values):
            raise ValueError("non-finite Binance candle value")
        if (open_ms >= close_ms or min(op, high, low, close) <= 0
                or low > min(op, close) or high < max(op, close)
                or min(base_vol, quote_vol, buy_base, buy_quote) < 0
                or buy_base > base_vol or buy_quote > quote_vol or trades < 0):
            raise ValueError("inconsistent Binance candle values")
        if close_ms >= as_of_ms:
            continue
        result.append(Candle(open_ms, close_ms, op, high, low, close,
                             base_vol, quote_vol, trades, buy_base, buy_quote))
    if not result:
        raise ValueError("no completed candles available")
    for previous, current in zip(result, result[1:]):
        if current.open_time_ms <= previous.open_time_ms:
            raise ValueError("candles must be ordered with no duplicate open times")
    return result


def fetch_completed_candles(config: MarketConfig, *, as_of_ms: int | None = None) -> list[Candle]:
    """Fetch recent public candles; caller may pass a cutoff for replay/tests.

    A historical cutoff alone is not enough to obtain old data: this endpoint
    returns recent rows unless startTime/endTime are supplied. Backtests should
    use a separately archived historical dataset.
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if as_of_ms is None:
        as_of_ms = now_ms
    if as_of_ms > now_ms:
        raise ValueError("as_of_ms cannot be in the future")
    query = urlencode({"symbol": config.symbol, "interval": config.interval,
                       "limit": config.candle_limit})
    url = f"{config.base_url.rstrip('/')}/api/v3/klines?{query}"
    with urlopen(url, timeout=config.timeout_seconds) as response:
        rows = json.load(response)
    if not isinstance(rows, list):
        raise ValueError("unexpected Binance response")
    return parse_candles(rows, as_of_ms=as_of_ms)


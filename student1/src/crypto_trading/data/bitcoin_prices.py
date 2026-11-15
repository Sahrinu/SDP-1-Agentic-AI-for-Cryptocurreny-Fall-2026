"""Download completed UTC BTC/USDT daily Spot candles for reproducible analysis."""

import csv
import json
from decimal import Decimal, InvalidOperation
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from crypto_trading.config import MarketConfig
from crypto_trading.data.binance import Candle, parse_candles

DAY_MS = 86_400_000
CSV_FIELDS = ("date", "open_time_ms", "close_time_ms", "open", "high", "low",
              "close", "volume_btc", "volume_usdt", "trade_count",
              "taker_buy_btc", "taker_buy_usdt")


def fetch_daily_history(start: date, end: date, *, config: MarketConfig | None = None,
                        today: date | None = None) -> list[Candle]:
    """Fetch inclusive dates; reject unfinished days and gaps between returned bars."""
    config = config or MarketConfig(interval="1d")
    if config.symbol != "BTCUSDT" or config.interval != "1d":
        raise ValueError("daily history requires BTCUSDT and 1d candles")
    today = today or datetime.now(UTC).date()
    if start > end or end >= today:
        raise ValueError("date range must end before the current UTC day")
    cursor = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp() * 1000) - 1
    candles: list[Candle] = []
    while cursor <= end_ms:
        query = urlencode({"symbol": "BTCUSDT", "interval": "1d", "startTime": cursor,
                           "endTime": end_ms, "limit": 1000})
        url = f"{config.base_url.rstrip('/')}/api/v3/klines?{query}"
        with urlopen(url, timeout=config.timeout_seconds) as response:
            rows = json.load(response)
        if not isinstance(rows, list):
            raise ValueError("unexpected Binance response")
        if not rows:
            break
        page = parse_candles(rows, as_of_ms=end_ms + 1)
        for candle in page:
            if candle.open_time_ms < cursor or candle.open_time_ms > end_ms:
                raise ValueError("Binance returned a candle outside the requested page")
            if candle.open_time_ms % DAY_MS or candle.close_time_ms != candle.open_time_ms + DAY_MS - 1:
                raise ValueError("expected UTC daily candles")
            if candles and candle.open_time_ms != candles[-1].open_time_ms + DAY_MS:
                raise ValueError("gap or duplicate in daily price history")
            candles.append(candle)
        next_cursor = page[-1].open_time_ms + DAY_MS
        if next_cursor <= cursor:
            raise ValueError("Binance pagination made no progress")
        cursor = next_cursor
    if not candles:
        raise ValueError("no completed BTCUSDT candles in requested range")
    if candles[0].open_time_ms != int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000):
        raise ValueError("daily price history does not start on the requested date")
    if candles[-1].close_time_ms != end_ms:
        raise ValueError("daily price history does not reach the requested end date")
    return candles


def save_daily_history(candles: list[Candle], path: Path) -> None:
    """Write the source values used by both agents as a portable CSV."""
    if not candles:
        raise ValueError("cannot save an empty history")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for candle in candles:
            writer.writerow({"date": datetime.fromtimestamp(candle.open_time_ms / 1000, UTC).date().isoformat(),
                             **{field: getattr(candle, field) for field in CSV_FIELDS if field != "date"}})


def load_daily_history(path: Path) -> list[Candle]:
    """Load the saved BTC/USDT daily candles, rejecting malformed records."""
    candles = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(CSV_FIELDS):
            raise ValueError("unexpected price history CSV columns")
        for row in reader:
            values = [row[k] for k in ("open", "high", "low", "close", "volume_btc",
                                          "volume_usdt", "trade_count", "taker_buy_btc", "taker_buy_usdt")]
            try:
                candle = Candle(int(row["open_time_ms"]), int(row["close_time_ms"]),
                                *(Decimal(v) for v in values[:6]), int(values[6]),
                                Decimal(values[7]), Decimal(values[8]))
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise ValueError("invalid saved daily candle field") from exc
            day = datetime.fromtimestamp(candle.open_time_ms / 1000, UTC).date()
            if (row["date"] != day.isoformat() or candle.open_time_ms % DAY_MS
                    or candle.close_time_ms != candle.open_time_ms + DAY_MS - 1
                    or not all(v.is_finite() for v in (candle.open, candle.high, candle.low,
                            candle.close, candle.volume_btc, candle.volume_usdt,
                            candle.taker_buy_btc, candle.taker_buy_usdt))
                    or min(candle.open, candle.high, candle.low, candle.close) <= 0
                    or candle.low > min(candle.open, candle.close)
                    or candle.high < max(candle.open, candle.close)
                    or min(candle.volume_btc, candle.volume_usdt,
                           candle.taker_buy_btc, candle.taker_buy_usdt) < 0
                    or candle.taker_buy_btc > candle.volume_btc
                    or candle.taker_buy_usdt > candle.volume_usdt
                    or candle.trade_count < 0):
                raise ValueError("invalid daily candle date or close time")
            if candles and candle.open_time_ms != candles[-1].open_time_ms + DAY_MS:
                raise ValueError("gap or duplicate in saved daily price history")
            candles.append(candle)
    if not candles:
        raise ValueError("saved price history is empty")
    return candles

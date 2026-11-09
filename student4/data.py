"""Collect and validate USD stablecoin quotes from CoinGecko."""

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .peg import derive_depeg_signal

COINS = {"tether": "USDT", "usd-coin": "USDC", "dai": "DAI"}


class DataError(ValueError):
    """Missing, invalid, stale, or unavailable market data."""


def utc_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise DataError("Timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def number(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise DataError(f"Invalid {name}: expected a finite {'positive' if positive else 'nonnegative'} number")
    return value


@dataclass(frozen=True)
class StablecoinQuote:
    coin_id: str
    symbol: str
    price_usd: float
    market_cap_usd: float
    volume_24h_usd: float
    updated_at: datetime

    @property
    def peg_deviation_pct(self) -> float:
        signal = derive_depeg_signal(self.price_usd)
        if signal is None:
            raise DataError(f"Invalid {self.symbol} peg price")
        return float(signal.abs_raw_bps / 100)


@dataclass(frozen=True)
class StablecoinSnapshot:
    observed_at: datetime
    source: str
    quotes: tuple[StablecoinQuote, ...]

    @classmethod
    def from_payload(cls, payload: dict, *, observed_at: datetime, source: str):
        if observed_at.tzinfo is None:
            raise DataError("Observation time must include a timezone")
        if not isinstance(payload, dict):
            raise DataError("Expected a CoinGecko JSON object")
        quotes = []
        for coin_id, symbol in COINS.items():
            try:
                row = payload[coin_id]
                updated = number(row["last_updated_at"], "last_updated_at", positive=True)
                quote = StablecoinQuote(
                    coin_id, symbol,
                    number(row["usd"], f"{symbol} price", positive=True),
                    number(row["usd_market_cap"], f"{symbol} market cap", positive=True),
                    number(row["usd_24h_vol"], f"{symbol} volume"),
                    datetime.fromtimestamp(updated, timezone.utc),
                )
            except (KeyError, TypeError, OverflowError, OSError) as exc:
                raise DataError(f"Missing or invalid data for {symbol}") from exc
            quotes.append(quote)
        return cls(observed_at.astimezone(timezone.utc), source, tuple(quotes))

    @property
    def total_market_cap_usd(self) -> float:
        return sum(quote.market_cap_usd for quote in self.quotes)

    def validate_freshness(self, now: datetime, max_age_seconds: float = 900):
        if now.tzinfo is None:
            raise DataError("Current time must include a timezone")
        if {q.coin_id for q in self.quotes} != set(COINS) or len(self.quotes) != len(COINS):
            raise DataError("Snapshot must contain USDT, USDC, and DAI exactly once")
        for label, timestamp in [("snapshot", self.observed_at)] + [
            (q.symbol, q.updated_at) for q in self.quotes
        ]:
            age = (now - timestamp).total_seconds()
            if age < -60 or age > max_age_seconds:
                raise DataError(f"{label} data is stale or has a future timestamp")

    def to_dict(self) -> dict:
        return {
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
            "coins": {q.coin_id: {
                "usd": q.price_usd,
                "usd_market_cap": q.market_cap_usd,
                "usd_24h_vol": q.volume_24h_usd,
                "last_updated_at": int(q.updated_at.timestamp()),
            } for q in self.quotes},
        }

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls.from_payload(raw["coins"], observed_at=utc_time(raw["observed_at"]), source=raw["source"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"Invalid snapshot file: {path}") from exc


class CoinGeckoClient:
    """One bounded request; failures never silently substitute synthetic quotes."""

    def __init__(self, api_key: str | None = None, timeout: float = 15):
        self.api_key = api_key or os.environ.get("COINGECKO_DEMO_API_KEY")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.timeout = timeout

    def fetch(self) -> StablecoinSnapshot:
        if not self.api_key:
            raise DataError("Set COINGECKO_DEMO_API_KEY to use live data; use demo for offline data")
        params = urlencode({
            "ids": ",".join(COINS), "vs_currencies": "usd",
            "include_market_cap": "true", "include_24hr_vol": "true",
            "include_last_updated_at": "true", "precision": "full",
        })
        request = Request(
            "https://api.coingecko.com/api/v3/simple/price?" + params,
            headers={"x-cg-demo-api-key": self.api_key, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise DataError(f"CoinGecko returned HTTP {exc.code}; check your key or rate limit") from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise DataError("CoinGecko request failed or returned invalid JSON") from exc
        now = datetime.now(timezone.utc)
        snapshot = StablecoinSnapshot.from_payload(payload, observed_at=now, source="coingecko")
        snapshot.validate_freshness(now)
        return snapshot

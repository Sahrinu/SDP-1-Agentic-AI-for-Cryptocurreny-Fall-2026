from dataclasses import dataclass


@dataclass(frozen=True)
class MarketConfig:
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    candle_limit: int = 200
    base_url: str = "https://data-api.binance.vision"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.symbol.isalnum() or not self.symbol.isupper():
            raise ValueError("symbol must be an uppercase Binance symbol such as BTCUSDT")
        if self.interval not in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}:
            raise ValueError("unsupported candle interval")
        if not 100 <= self.candle_limit <= 1000:
            raise ValueError("candle_limit must be between 100 and 1000")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


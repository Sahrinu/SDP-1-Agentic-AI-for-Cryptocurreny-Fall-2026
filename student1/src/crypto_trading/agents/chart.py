"""Chart specialist built on the existing, tested market indicators."""

from crypto_trading.agents.market import MarketReport, analyze_market
from crypto_trading.data.binance import Candle

ChartReport = MarketReport


def analyze_chart(candles: list[Candle], *, symbol: str = "BTCUSDT",
                  interval: str = "15m") -> ChartReport:
    """Describe price trend, momentum, volatility and volume without placing a trade."""
    if symbol != "BTCUSDT":
        raise ValueError("this version of the Chart agent covers BTCUSDT only")
    return analyze_market(candles, symbol=symbol, interval=interval)

"""Market analyst: an evidence-based assessment, not an order instruction."""

from dataclasses import asdict, dataclass

from crypto_trading.data.binance import Candle
from crypto_trading.features.technical import calculate_features


@dataclass(frozen=True)
class MarketReport:
    symbol: str
    interval: str
    as_of_utc: str
    source: str
    trend: str
    momentum: str
    volatility: str
    participation: str
    evidence: dict[str, float | None]
    limitations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_market(candles: list[Candle], *, symbol: str, interval: str) -> MarketReport:
    features = calculate_features(candles)
    if features.close > features.ema20 > features.ema50:
        trend = "up"
    elif features.close < features.ema20 < features.ema50:
        trend = "down"
    else:
        trend = "mixed"
    # RSI is left to be evaluated by the llm, taken out of decision for momentum (before: momentum= "+" when features.macd > features.macd_signal and features.rsi > 50) papers indicate common RSI do not provide any advantage.
    if features.macd > features.macd_signal:
        momentum = "positive"
    elif features.macd < features.macd_signal:
        momentum = "negative"
    else:
        momentum = "mixed"
    volatility = "elevated" if features.atr_pct >= 3 else "normal"
    participation = ("high" if features.volume_ratio20 >= 1.5 else
                     "low" if features.volume_ratio20 < 0.5 else "normal")
    evidence = {key: round(value, 6) if value is not None else None
                for key, value in asdict(features).items()}
    return MarketReport(
        symbol=symbol, interval=interval, as_of_utc=candles[-1].close_time,
        source="Binance Spot completed candles", trend=trend,
        momentum=momentum, volatility=volatility, participation=participation,
        evidence=evidence,
        limitations=["This is a market assessment, not a trade order.",
                     "Current bid/ask and on-chain fundamentals are not included."],
    )


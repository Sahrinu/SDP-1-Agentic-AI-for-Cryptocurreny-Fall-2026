"""Indicators calculated from completed candles, with no future data."""

from dataclasses import dataclass
from statistics import mean

from crypto_trading.data.binance import Candle


@dataclass(frozen=True)
class MarketFeatures:
    close: float
    ema20: float
    ema50: float
    rsi14: float
    macd: float
    macd_signal: float
    atr14: float
    atr_pct: float
    volume_ratio20: float
    taker_buy_share: float | None


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError(f"need at least {period} values")
    alpha = 2 / (period + 1)
    result = [mean(values[:period])]
    for value in values[period:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = [max(v, 0) for v in changes]
    losses = [max(-v, 0) for v in changes]
    avg_gain, avg_loss = mean(gains[:period]), mean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(candles: list[Candle], period: int = 14) -> float:
    ranges = []
    for previous, current in zip(candles, candles[1:]):
        hi, lo, prior_close = float(current.high), float(current.low), float(previous.close)
        ranges.append(max(hi - lo, abs(hi - prior_close), abs(lo - prior_close)))
    atr = mean(ranges[:period])
    for value in ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def calculate_features(candles: list[Candle]) -> MarketFeatures:
    if len(candles) < 60:
        raise ValueError("at least 60 completed candles are required")
    closes = [float(c.close) for c in candles]
    ema20, ema50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    ema12_all, ema26_all = _ema(closes, 12), _ema(closes, 26)
    macd_series = [a - b for a, b in zip(ema12_all[14:], ema26_all)]
    macd, signal = macd_series[-1], _ema(macd_series, 9)[-1]
    atr = _atr(candles)
    current_volume = float(candles[-1].volume_btc)
    prior_volume_average = mean(float(c.volume_btc) for c in candles[-21:-1])
    volume_ratio = current_volume / prior_volume_average if prior_volume_average else 0.0
    quote_volume = float(candles[-1].volume_usdt)
    taker_share = float(candles[-1].taker_buy_usdt) / quote_volume if quote_volume else None
    return MarketFeatures(closes[-1], ema20, ema50, _rsi(closes), macd, signal,
                          atr, 100 * atr / closes[-1], volume_ratio, taker_share)


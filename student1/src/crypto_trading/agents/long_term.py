"""Experimental Bitcoin long-horizon price trend; no next-day trading signal."""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from math import exp, isfinite, log

from crypto_trading.data.binance import Candle

GENESIS = date(2009, 1, 3)


@dataclass(frozen=True)
class LongTermReport:
    symbol: str
    as_of_utc: str
    source: str
    method: str
    observations: int
    first_utc_date: str
    latest_close_usdt: float
    fitted_trend_usdt: float
    deviation_pct: float
    fitted_one_year_trend_usdt: float
    r_squared_in_log_space: float
    assessment: str
    limitations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_long_term(candles: list[Candle], *, as_of: date | None = None) -> LongTermReport:
    """Fit log(price) against log(days since genesis) using data through as_of."""
    as_of = as_of or datetime.now(UTC).date()
    rows = []
    previous_day = None
    for candle in candles:
        day = datetime.fromtimestamp(candle.open_time_ms / 1000, UTC).date()
        if day > as_of:
            continue
        if previous_day is not None and day <= previous_day:
            raise ValueError("price history must be ordered without duplicates")
        if day <= GENESIS or not candle.close.is_finite() or candle.close <= 0:
            raise ValueError("price history has an invalid day or close")
        rows.append((day, float(candle.close), candle))
        previous_day = day
    if len(rows) < 365 or (rows[-1][0] - rows[0][0]).days < 730:
        raise ValueError("long-term analysis requires 365 daily prices across at least two years")
    if (as_of - rows[-1][0]).days > 3:
        raise ValueError("latest price is more than three days old at the requested cutoff")
    x = [log((day - GENESIS).days) for day, _, _ in rows]
    y = [log(price) for _, price, _ in rows]
    xbar, ybar = sum(x) / len(x), sum(y) / len(y)
    sxx = sum((v - xbar) ** 2 for v in x)
    if sxx == 0:
        raise ValueError("price history has insufficient time variation")
    slope = sum((a - xbar) * (b - ybar) for a, b in zip(x, y)) / sxx
    intercept = ybar - slope * xbar
    residual = sum((actual - (intercept + slope * age)) ** 2 for age, actual in zip(x, y))
    total = sum((actual - ybar) ** 2 for actual in y)
    r_squared = 1 - residual / total if total else 0.0
    trend = exp(intercept + slope * x[-1])
    one_year_age = log((rows[-1][0] - GENESIS).days + 365)
    one_year_trend = exp(intercept + slope * one_year_age)
    deviation = 100 * (rows[-1][1] / trend - 1)
    if not all(isfinite(v) for v in (trend, one_year_trend, deviation, r_squared)):
        raise ValueError("long-term fit produced non-finite values")
    assessment = "above fitted trend" if deviation > 10 else "below fitted trend" if deviation < -10 else "near fitted trend"
    return LongTermReport(
        symbol="BTCUSDT", as_of_utc=rows[-1][2].close_time,
        source="Binance Spot completed UTC daily BTCUSDT candles",
        method="Ordinary least squares: log(close in USDT) versus log(days since 2009-01-03)",
        observations=len(rows), first_utc_date=rows[0][0].isoformat(),
        latest_close_usdt=round(rows[-1][1], 2), fitted_trend_usdt=round(trend, 2),
        deviation_pct=round(deviation, 2), fitted_one_year_trend_usdt=round(one_year_trend, 2),
        r_squared_in_log_space=round(r_squared, 4), assessment=assessment,
        limitations=["An exploratory historical fit, not a one-day price forecast or trade signal.",
                     "The one-year value is an extrapolated fitted line, not an expected market price.",
                     "BTCUSDT is a Binance trading pair; this fit is not the same as a BTC/USD index.",
                     "Historical association does not establish an edge; validate out of sample."],
    )

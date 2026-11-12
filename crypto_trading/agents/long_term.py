"""Point-in-time Bitcoin price power-law context, without a daily trade signal.

The model family follows Santostasi and Perrenod (2026):
log10(USD price) = intercept + exponent * log10(days since 2009-01-03).
This implementation refits the two coefficients at each cutoff. It does not
reuse coefficients estimated with prices that were unavailable at that cutoff.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

GENESIS_DATE = date(2009, 1, 3)
FIRST_RELIABLE_PRICE_DATE = date(2010, 7, 17)
MIN_OBSERVATIONS = 365
MIN_SPAN_DAYS = 730
MAX_STALENESS_DAYS = 7


@dataclass(frozen=True)
class DailyPrice:
    day: date
    close: float


@dataclass(frozen=True)
class LongTermReport:
    asset: str
    quote_currency: str
    as_of_date: str
    latest_price_date: str
    latest_close: float
    source: str
    model: str
    fit_start_date: str
    fit_end_date: str
    observations: int
    exponent: float
    intercept: float
    r_squared: float
    residual_std_dex: float
    trend_at_latest_price: float
    price_to_trend_ratio: float
    deviation_log10: float
    horizon_days: int
    horizon_date: str
    horizon_trend: float
    limitations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def load_daily_prices_csv(path: str | Path) -> list[DailyPrice]:
    """Read the replay's date,close format; caller supplies the data source."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"date", "close"} <= {name.strip().lower() for name in reader.fieldnames}:
            raise ValueError("price CSV needs date and close columns")
        rows = []
        for number, raw in enumerate(reader, start=2):
            row = {key.strip().lower(): value for key, value in raw.items() if key}
            try:
                day = date.fromisoformat(row["date"].strip())
                close = float(row["close"])
            except (KeyError, AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid price on CSV line {number}") from exc
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f"close must be finite and positive on CSV line {number}")
            rows.append(DailyPrice(day, close))
    return rows


def _trend(intercept: float, exponent: float, day: date) -> float:
    age = (day - GENESIS_DATE).days
    if age <= 0:
        raise ValueError("power law requires a date after the Genesis Block")
    value = 10 ** (intercept + exponent * math.log10(age))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("power-law trend is outside the numeric range")
    return value


def analyze_long_term(
    prices: list[DailyPrice], *, as_of: date, horizon_days: int = 365,
    quote_currency: str = "USDT",
    source: str = "supplied historical BTC daily closes",
) -> LongTermReport:
    """Fit only completed daily closes on or before ``as_of``.

    ``as_of`` is the last completed UTC price day, not the current intraday
    clock time. A full-year horizon gives context, not a next-day forecast.
    """
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise ValueError("as_of must be the last completed UTC date")
    if not isinstance(horizon_days, int) or isinstance(horizon_days, bool) or horizon_days < 30:
        raise ValueError("horizon_days must be an integer of at least 30")
    if quote_currency not in {"USD", "USDT"}:
        raise ValueError("quote_currency must be USD or USDT")
    if as_of > datetime.now(UTC).date():
        raise ValueError("as_of cannot be in the future")

    eligible = sorted(
        (point for point in prices if FIRST_RELIABLE_PRICE_DATE <= point.day <= as_of),
        key=lambda point: point.day,
    )
    if len(eligible) < MIN_OBSERVATIONS:
        raise ValueError(f"at least {MIN_OBSERVATIONS} historical daily prices are required")
    if (eligible[-1].day - eligible[0].day).days < MIN_SPAN_DAYS:
        raise ValueError(f"price history must span at least {MIN_SPAN_DAYS} days")
    if (as_of - eligible[-1].day).days > MAX_STALENESS_DAYS:
        raise ValueError("latest price is too old for the requested as_of date")
    if any(current.day == previous.day for previous, current in zip(eligible, eligible[1:])):
        raise ValueError("price history contains duplicate dates")
    if any(isinstance(point.close, bool) or not math.isfinite(point.close)
           or point.close <= 0 for point in eligible):
        raise ValueError("all eligible prices must be finite and positive")

    x = [math.log10((point.day - GENESIS_DATE).days) for point in eligible]
    y = [math.log10(point.close) for point in eligible]
    mean_x, mean_y = math.fsum(x) / len(x), math.fsum(y) / len(y)
    centered_x = [value - mean_x for value in x]
    centered_y = [value - mean_y for value in y]
    xx = math.fsum(value * value for value in centered_x)
    yy = math.fsum(value * value for value in centered_y)
    if xx <= 0 or yy <= 0:
        raise ValueError("price history has insufficient variation for a power-law fit")
    exponent = math.fsum(a * b for a, b in zip(centered_x, centered_y)) / xx
    intercept = mean_y - exponent * mean_x
    residuals = [actual - (intercept + exponent * age) for age, actual in zip(x, y)]
    squared_error = math.fsum(residual * residual for residual in residuals)
    r_squared = max(0.0, min(1.0, 1 - squared_error / yy))
    residual_std_dex = math.sqrt(squared_error / (len(eligible) - 2))

    latest = eligible[-1]
    trend_now = _trend(intercept, exponent, latest.day)
    horizon_date = as_of + timedelta(days=horizon_days)
    horizon_trend = _trend(intercept, exponent, horizon_date)
    return LongTermReport(
        asset="BTC", quote_currency=quote_currency, as_of_date=as_of.isoformat(), latest_price_date=latest.day.isoformat(),
        latest_close=latest.close, source=source,
        model="OLS log10(price in quote currency) versus log10(days since 2009-01-03)",
        fit_start_date=eligible[0].day.isoformat(), fit_end_date=latest.day.isoformat(),
        observations=len(eligible), exponent=exponent, intercept=intercept,
        r_squared=r_squared, residual_std_dex=residual_std_dex,
        trend_at_latest_price=trend_now,
        price_to_trend_ratio=latest.close / trend_now,
        deviation_log10=math.log10(latest.close / trend_now),
        horizon_days=horizon_days, horizon_date=horizon_date.isoformat(),
        horizon_trend=horizon_trend,
        limitations=[
            "The fitted curve describes historical long-run price; it is not a next-day probability or trade instruction.",
            "Coefficients are refitted only from data available by the cutoff and can change as new prices arrive.",
            "High in-sample fit does not establish out-of-sample trading advantage or a true structural law.",
            "Price can stay far above or below the trend for long periods; this is not a timing signal.",
            "The horizon value is the fitted trend, not a guaranteed future price or prediction interval.",
        ] + (["BTC/USDT is used as a USD proxy; USDT peg changes can affect the comparison with USD research."]
             if quote_currency == "USDT" else []),
    )

import io
import json
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from crypto_trading.agents.chart import analyze_chart
from crypto_trading.agents.long_term import analyze_long_term
from crypto_trading.data.binance import Candle
from crypto_trading.data.bitcoin_prices import (DAY_MS, fetch_daily_history,
                                               load_daily_history, save_daily_history)


def candle(day: date, price: int) -> Candle:
    open_ms = int(datetime.combine(day, datetime.min.time(), UTC).timestamp() * 1000)
    p = Decimal(price)
    return Candle(open_ms, open_ms + DAY_MS - 1, p, p + 1, p - 1, p,
                  Decimal(10), p * 10, 20, Decimal(5), p * 5)


def api_row(item: Candle) -> list:
    return [item.open_time_ms, str(item.open), str(item.high), str(item.low),
            str(item.close), str(item.volume_btc), item.close_time_ms,
            str(item.volume_usdt), item.trade_count, str(item.taker_buy_btc),
            str(item.taker_buy_usdt), "0"]


class StudentOneTests(unittest.TestCase):
    def test_price_history_paginates_and_round_trips(self):
        start = date(2024, 1, 1)
        source = [candle(start + timedelta(days=i), 40000 + i) for i in range(3)]
        pages = [io.BytesIO(json.dumps([api_row(c) for c in source[:2]]).encode()),
                 io.BytesIO(json.dumps([api_row(source[2])]).encode())]
        with patch("crypto_trading.data.bitcoin_prices.urlopen", side_effect=pages) as request:
            result = fetch_daily_history(start, start + timedelta(days=2), today=date(2024, 1, 5))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result, source)
        path = Path(__file__).resolve().parents[1] / "outputs" / "test_prices.csv"
        try:
            save_daily_history(result, path)
            self.assertEqual(load_daily_history(path), source)
        finally:
            path.unlink(missing_ok=True)

    def test_current_day_and_missing_daily_bar_are_rejected(self):
        with self.assertRaises(ValueError):
            fetch_daily_history(date(2024, 1, 1), date(2024, 1, 5), today=date(2024, 1, 5))
        start = date(2024, 1, 1)
        rows = [api_row(candle(start, 40000)), api_row(candle(start + timedelta(days=2), 40002))]
        with patch("crypto_trading.data.bitcoin_prices.urlopen",
                   return_value=io.BytesIO(json.dumps(rows).encode())):
            with self.assertRaisesRegex(ValueError, "gap"):
                fetch_daily_history(start, start + timedelta(days=2), today=date(2024, 1, 5))

    def test_chart_uses_existing_indicator_report(self):
        start = date(2024, 1, 1)
        candles = [candle(start + timedelta(days=i), 40000 + i * 10) for i in range(70)]
        report = analyze_chart(candles, interval="1d")
        self.assertEqual(report.trend, "up")
        self.assertEqual(report.evidence["close"], 40690)

    def test_long_term_respects_historical_cutoff(self):
        start = date(2020, 1, 1)
        history = [candle(start + timedelta(days=i), 7000 + i * 20) for i in range(900)]
        cutoff = start + timedelta(days=799)
        before = analyze_long_term(history[:800], as_of=cutoff)
        after = analyze_long_term(history, as_of=cutoff)
        self.assertEqual(before, after)
        self.assertEqual(after.observations, 800)
        self.assertIn("trade signal", after.limitations[0])

    def test_long_term_rejects_short_history(self):
        start = date(2020, 1, 1)
        history = [candle(start + timedelta(days=i), 7000 + i) for i in range(100)]
        with self.assertRaisesRegex(ValueError, "365"):
            analyze_long_term(history, as_of=start + timedelta(days=99))


if __name__ == "__main__":
    unittest.main()

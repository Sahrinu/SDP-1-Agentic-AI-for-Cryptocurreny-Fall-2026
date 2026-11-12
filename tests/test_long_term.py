import math
import unittest
from datetime import date, timedelta

from crypto_trading.agents.long_term import (
    DailyPrice, GENESIS_DATE, analyze_long_term, load_daily_prices_csv,
)


class LongTermTests(unittest.TestCase):
    def setUp(self):
        start = date(2018, 1, 1)
        self.prices = [
            DailyPrice(start + timedelta(days=i),
                       10 ** (-15.5 + 5.5 * math.log10((start + timedelta(days=i) - GENESIS_DATE).days)))
            for i in range(1000)
        ]
        self.cutoff = self.prices[-1].day

    def test_recovers_power_law_from_past_prices(self):
        report = analyze_long_term(self.prices, as_of=self.cutoff)
        self.assertAlmostEqual(report.exponent, 5.5, places=8)
        self.assertAlmostEqual(report.r_squared, 1.0, places=8)
        self.assertAlmostEqual(report.price_to_trend_ratio, 1.0, places=8)
        self.assertEqual(report.observations, 1000)
        self.assertEqual(report.horizon_days, 365)

    def test_future_price_does_not_change_historical_fit(self):
        normal = analyze_long_term(self.prices, as_of=self.cutoff)
        poisoned = analyze_long_term(
            self.prices + [DailyPrice(self.cutoff + timedelta(days=1), 1e15)],
            as_of=self.cutoff,
        )
        self.assertEqual(normal.to_dict(), poisoned.to_dict())

    def test_requires_long_history_and_a_long_horizon(self):
        with self.assertRaisesRegex(ValueError, "span"):
            analyze_long_term(self.prices[:500], as_of=self.prices[499].day)
        with self.assertRaisesRegex(ValueError, "horizon_days"):
            analyze_long_term(self.prices, as_of=self.cutoff, horizon_days=1)

    def test_reads_replay_price_csv(self):
        prices = load_daily_prices_csv("tests/fake_data/btc_prices.csv")
        self.assertGreater(len(prices), 30)
        self.assertGreater(prices[0].close, 0)


if __name__ == "__main__":
    unittest.main()

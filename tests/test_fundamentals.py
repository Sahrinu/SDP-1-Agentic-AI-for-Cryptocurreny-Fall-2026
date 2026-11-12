import json
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from crypto_trading.agents.fundamentals import analyze_fundamentals
from crypto_trading.agents.fundamentals_llm import (
    build_fundamentals_prompt,
    parse_fundamentals_interpretation,
)
from crypto_trading.data.coinmetrics import OnchainDay, parse_onchain_days


class FundamentalsTests(unittest.TestCase):
    def test_future_and_recent_daily_values_are_excluded(self):
        rows = [{"asset": "btc", "time": f"2026-10-{day:02d}T00:00:00.000000000Z",
                 "TxCnt": "100", "AdrActCnt": "80", "HashRate": "1000", "FeeTotNtv": "2.5"}
                for day in (20, 21, 22)]
        days = parse_onchain_days(rows, as_of=datetime(2026, 10, 23, tzinfo=UTC))
        self.assertEqual([d.day for d in days], [date(2026, 10, 20), date(2026, 10, 21)])

    def test_invalid_metric_value_is_rejected(self):
        rows = [{"asset": "btc", "time": "2026-10-20T00:00:00Z", "TxCnt": "-1",
                 "AdrActCnt": "80", "HashRate": "1000", "FeeTotNtv": "2.5"}]
        with self.assertRaises(ValueError):
            parse_onchain_days(rows, as_of=datetime(2026, 10, 23, tzinfo=UTC))

    def test_report_compares_latest_with_preceding_week(self):
        start = date(2026, 10, 20)
        days = [OnchainDay(start + timedelta(days=i), 100 if i < 7 else 120,
                           80, Decimal("1000"), Decimal("2")) for i in range(8)]
        report = analyze_fundamentals(days, as_of=datetime(2026, 10, 29, tzinfo=UTC))
        self.assertEqual(report.evidence["transactions"]["change_pct"], 20.0)
        self.assertEqual(report.evidence["transactions"]["prior_7d_mean"], 100)
        self.assertEqual(report.latest_metric_date, "2026-10-27")
        self.assertNotIn("trade_action", report.to_dict())

    def test_prompt_and_model_response_contract(self):
        start = date(2026, 10, 20)
        days = [OnchainDay(start + timedelta(days=i), 100, 80, Decimal("1000"), Decimal("2"))
                for i in range(8)]
        report = analyze_fundamentals(days, as_of=datetime(2026, 10, 29, tzinfo=UTC))
        prompt = build_fundamentals_prompt(report)
        self.assertIn("Separate bull and bear agents", prompt)
        self.assertIn('"transactions":', prompt)
        raw = json.dumps({"summary": "Activity was steady.", "network_analysis": "Counts were flat.",
                          "mining_analysis": "Hash rate was flat.", "key_observations": ["Fees were flat."],
                          "limitations": ["Publication times are unknown."], "confidence": "medium"})
        self.assertEqual(parse_fundamentals_interpretation(raw).confidence, "medium")
        with self.assertRaises(ValueError):
            parse_fundamentals_interpretation('{"summary":"buy"}')


if __name__ == "__main__":
    unittest.main()

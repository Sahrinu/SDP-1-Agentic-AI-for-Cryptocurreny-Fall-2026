"""Offline checks for point-in-time specialist inputs and replay contracts."""
import unittest

from crypto_trading.ensemble.engine import Exchange
from crypto_trading.agents.news_specialist import filter_articles_for_prediction, run_news_specialist
from crypto_trading.agents.mood_specialist import parse_fng_response, run_mood_specialist


class ContractTests(unittest.TestCase):
    def test_future_news_is_excluded_even_if_listed_first(self):
        articles = [
            {"evidence_id": "future", "title": "Bitcoin rises", "publication_timestamp": "2025-01-02T00:00:00Z", "summary": "Bitcoin rises"},
            {"evidence_id": "past", "title": "Bitcoin rises", "publication_timestamp": "2025-01-01T00:00:00Z", "summary": "Bitcoin rises"},
        ]
        selected, flags = filter_articles_for_prediction(articles, "2025-01-01T12:00:00Z")
        self.assertEqual([item["evidence_id"] for item in selected], ["past"])
        self.assertEqual(flags["excluded_after_prediction_time"], ["future"])

    def test_news_rejects_unsupplied_citation(self):
        articles = [{"evidence_id": "past", "title": "Bitcoin rises", "publication_timestamp": "2025-01-01T00:00:00Z", "summary": "Bitcoin rises"}]
        def model(_name, _prompt):
            return {"agent": "News Specialist", "prediction_time": "2025-01-01T12:00:00Z", "target_time": "2025-01-02T12:00:00Z", "probability_up": 0.8, "explanation": "Claim", "evidence_ids": ["unknown"], "data_status": "sufficient_data", "limitations": []}
        with self.assertRaisesRegex(ValueError, "evidence"):
            run_news_specialist(articles, "2025-01-01T12:00:00Z", "2025-01-02T12:00:00Z", model_invoker=model)

    def test_fear_greed_history_stops_at_prediction_time(self):
        raw = {"data": [
            {"timestamp": "1735689600", "value": "20"},
            {"timestamp": "1735862400", "value": "80"},
        ]}
        readings = parse_fng_response(raw, "2025-01-02T00:00:00Z")
        self.assertEqual([r["score"] for r in readings], [20])
        result = run_mood_specialist(readings, "2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z")
        self.assertEqual(result.probability_up, 0.5)
        self.assertIn("placeholder", result.limitations[0].lower())

    def test_exchange_rewards_calibrated_probability(self):
        good = Exchange(["a"])
        extreme = Exchange(["a"])
        for outcome in [True, True, False, True, False] * 5:
            good.settle({"a": 0.6}, outcome)
            extreme.settle({"a": 0.95}, outcome)
        self.assertGreater(good.coins["a"], extreme.coins["a"])


if __name__ == "__main__":
    unittest.main()

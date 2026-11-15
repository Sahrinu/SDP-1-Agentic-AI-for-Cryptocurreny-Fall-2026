import json
import unittest
from decimal import Decimal

from crypto_trading.agents.market import analyze_market
from crypto_trading.agents.market_llm import build_market_prompt, parse_market_interpretation
from crypto_trading.data.binance import Candle, parse_candles


class MarketTests(unittest.TestCase):
    def test_unfinished_candle_is_excluded(self):
        rows = [
            [0, "100", "102", "99", "101", "10", 999, "1000", 20, "5", "500", "0"],
            [1000, "101", "103", "100", "102", "10", 1999, "1000", 20, "5", "500", "0"],
        ]
        candles = parse_candles(rows, as_of_ms=1500)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, Decimal("101"))

    def test_report_uses_latest_completed_candle_and_has_indicators(self):
        candles = []
        for i in range(70):
            price = Decimal(100 + i) + Decimal(i * i) / 100
            candles.append(Candle(i * 1000, i * 1000 + 999, price, price + 1,
                                  price - 1, price, Decimal(10), Decimal(1000),
                                  20, Decimal(5), Decimal(500)))
        report = analyze_market(candles, symbol="BTCUSDT", interval="15m")
        self.assertEqual(report.evidence["close"], 216.61)
        self.assertEqual(report.trend, "up")
        self.assertNotIn("bias", report.to_dict())
        self.assertTrue(report.as_of_utc.endswith("+00:00"))

    def test_bad_candle_is_rejected(self):
        row = [0, "100", "90", "99", "101", "10", 999,
               "1000", 20, "5", "500", "0"]
        with self.assertRaises(ValueError):
            parse_candles([row], as_of_ms=1000)

    def test_model_response_contract(self):
        valid = json.dumps({"summary": "Mixed signals.", "trend_analysis": "Trend is up.",
                            "momentum_analysis": "Momentum is mixed.",
                            "volatility_analysis": "ATR is moderate.",
                            "volume_analysis": "Volume is typical.",
                            "key_observations": ["Trend and momentum differ."],
                            "limitations": ["Only one snapshot is available."],
                            "confidence": "low"})
        self.assertEqual(parse_market_interpretation(valid).confidence, "low")
        with self.assertRaises(ValueError):
            parse_market_interpretation('{"summary":"Buy now"}')
        with self.assertRaises(ValueError):
            parse_market_interpretation(valid.replace('"confidence": "low"', '"confidence": []'))

    def test_prompt_delegates_debate_and_includes_only_report_facts(self):
        candles = []
        for i in range(70):
            price = Decimal(100 + i)
            candles.append(Candle(i * 1000, i * 1000 + 999, price, price + 1,
                                  price - 1, price, Decimal(10), Decimal(1000),
                                  20, Decimal(5), Decimal(500)))
        report = analyze_market(candles, symbol="BTCUSDT", interval="15m")
        prompt = build_market_prompt(report)
        self.assertIn("bull and bear agents", prompt.lower())
        self.assertIn("Do not create separate bullish and bearish arguments", prompt)
        self.assertIn('"symbol":"BTCUSDT"', prompt)
        self.assertIn('"rsi14":', prompt)

    def test_rsi_does_not_gate_macd_momentum(self):
        from dataclasses import replace
        from unittest.mock import patch
        from crypto_trading.features.technical import MarketFeatures

        candles = [Candle(0, 999, Decimal(100), Decimal(101), Decimal(99),
                          Decimal(100), Decimal(10), Decimal(1000), 20,
                          Decimal(5), Decimal(500))]
        features = MarketFeatures(close=100, ema20=99, ema50=98, rsi14=40,
                                  macd=2, macd_signal=1, atr14=1, atr_pct=1,
                                  volume_ratio20=1, taker_buy_share=0.5)
        with patch("crypto_trading.agents.market.calculate_features", return_value=features):
            low_rsi = analyze_market(candles, symbol="BTCUSDT", interval="15m")
        with patch("crypto_trading.agents.market.calculate_features",
                   return_value=replace(features, rsi14=70)):
            high_rsi = analyze_market(candles, symbol="BTCUSDT", interval="15m")
        self.assertEqual(low_rsi.momentum, "positive")
        self.assertEqual(high_rsi.momentum, "positive")


if __name__ == "__main__":
    unittest.main()

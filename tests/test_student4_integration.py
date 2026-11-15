"""Synthetic integration checks for replay availability and guarded execution."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from agents import stablecoin
from experiments.run import ask_agent, load_agents, load_data
from student4 import PaperTrader, StablecoinSnapshot
from student4.data import COINS
from student4.execution import LiquidityQuote, execute_with_quality
from student4.history import export_history


NOW = datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc)


class IntegrationTests(unittest.TestCase):
    def history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i, cap in enumerate((100, 102)):
                when = NOW - timedelta(days=1-i)
                payload = {coin: dict(usd=1, usd_market_cap=cap, usd_24h_vol=10,
                                      last_updated_at=when.timestamp()) for coin in COINS}
                snapshot = StablecoinSnapshot.from_payload(payload, observed_at=when, source="synthetic-test")
                path = root / f"{i}.json"
                snapshot.save(path)
                paths.append(path)
            export_history(paths, root / "stablecoin.csv")
            return load_data(root)

    def test_runner_discovers_and_accepts_stablecoin(self):
        agents = load_agents(Path(__file__).resolve().parents[1] / "agents")
        self.assertIn("stablecoin", agents)
        answer = ask_agent("stablecoin", agents["stablecoin"], pd.Timestamp("2026-11-07"), self.history())
        self.assertGreater(answer.probability, .5)
        self.assertLessEqual(answer.probability, .6)

    def test_unavailable_stale_invalid_and_duplicate_evidence_abstains(self):
        for mode in ("future-release", "stale", "invalid", "duplicate", "unchanged-quote", "missing"):
            data = self.history()
            frame = data["stablecoin"]
            if mode == "future-release":
                frame.loc[frame.index[-1], "available_at"] = "2026-11-08T00:01:00Z"
            elif mode == "stale":
                frame.loc[frame.index[-1], "observed_at"] = "2026-11-07T22:00:00Z"
            elif mode == "invalid":
                frame.loc[frame.index[-1], "usdt_price_usd"] = float("nan")
            elif mode == "duplicate":
                data["stablecoin"] = pd.concat([frame, frame.tail(1)])
            elif mode == "unchanged-quote":
                frame.loc[frame.index[-1], "usdt_updated_at"] = frame.iloc[0].usdt_updated_at
            else:
                data = {}
            with self.subTest(mode=mode):
                self.assertIsNone(ask_agent("stablecoin", stablecoin, pd.Timestamp("2026-11-07"), data).probability)

    def test_neutral_valid_supply_is_a_real_vote(self):
        data = self.history()
        for symbol in COINS.values():
            data["stablecoin"][symbol.lower() + "_market_cap_usd"] = 100
        result = stablecoin.forecast("2026-11-07", data)
        self.assertFalse(result["data_missing"])
        self.assertEqual(result["p_up"], .5)

    def test_future_rows_do_not_change_forecast(self):
        data = self.history()
        expected = stablecoin.forecast("2026-11-07", data)
        future = data["stablecoin"].tail(1).copy()
        future.index = pd.to_datetime(["2026-11-09"])
        future["usdt_price_usd"] = .5
        data["stablecoin"] = pd.concat([data["stablecoin"], future])
        self.assertEqual(expected, stablecoin.forecast("2026-11-07", data))

    def test_quality_buy_still_enforces_exposure_cap(self):
        trader = PaperTrader()
        quote = LiquidityQuote("59990", "60010", "1", NOW)
        decision, trade, reason = execute_with_quality(trader, "BUY", "60000", quote=quote, timestamp=NOW)
        self.assertIsNotNone(trade)
        self.assertLessEqual(decision.projected_exposure, .25)
        self.assertLessEqual(trader.btc * 60000 / (trader.cash + trader.btc * 60000), .25)

    def test_unusable_liquidity_does_not_mutate_account(self):
        for quote in (None, LiquidityQuote(59990, 60010, .000001, NOW),
                      LiquidityQuote(59990, 61000, 1, NOW),
                      LiquidityQuote(59990, 60010, 1, NOW-timedelta(seconds=31))):
            trader = PaperTrader()
            _, trade, reason = execute_with_quality(trader, "BUY", 60000, quote=quote, timestamp=NOW)
            self.assertIsNone(trade)
            self.assertTrue(reason.startswith("DEFER"))
            self.assertEqual(trader.cash, 10000)
            self.assertEqual(trader.btc, 0)

    def test_quality_gate_never_blocks_required_liquidation(self):
        trader = PaperTrader()
        trader.execute("BUY", 60000, timestamp=NOW)
        decision, trade, _ = execute_with_quality(trader, "HOLD", 60000, timestamp=NOW)
        self.assertEqual(decision.action, "SELL")
        self.assertIsNotNone(trade)
        self.assertEqual(trader.btc, 0)

"""Synthetic integration checks for replay availability and guarded execution."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from agents import stablecoin
from experiments.run import ask_agent, load_agents, load_data
from execution.paper_trading import PaperTrader
from market_data.stablecoin.data import StablecoinSnapshot
from market_data.stablecoin.data import COINS
from execution.quality import LiquidityQuote, execute_with_quality
from market_data.stablecoin.history import export_history
from risk.durable_gateway import RiskGateway, RiskPolicy

NOW = datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.risk_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.risk_tmp.cleanup)

    def configured(self, trader):
        policy = RiskPolicy("1", "1")  # Synthetic test thresholds.
        path = Path(self.risk_tmp.name) / "risk.sqlite3"
        RiskGateway.initialize_state(
            path,
            policy=policy,
            trader=trader,
            actor="synthetic-test",
            reason="test setup",
            timestamp=NOW - timedelta(minutes=1),
            previous_forecast="0.5",
        )
        gateway = RiskGateway(trader, policy, state_path=path)
        self.addCleanup(gateway.close)
        return trader

    def history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i, cap in enumerate((100, 102)):
                when = NOW - timedelta(days=1 - i)
                payload = {
                    coin: dict(
                        usd=1, usd_market_cap=cap, usd_24h_vol=10, last_updated_at=when.timestamp()
                    )
                    for coin in COINS
                }
                snapshot = StablecoinSnapshot.from_payload(
                    payload, observed_at=when, source="synthetic-test"
                )
                path = root / f"{i}.json"
                snapshot.save(path)
                paths.append(path)
            export_history(paths, root / "stablecoin.csv")
            return load_data(root)

    def test_runner_discovers_and_accepts_stablecoin(self):
        agents = load_agents(Path(__file__).resolve().parents[1] / "agents")
        self.assertIn("stablecoin", agents)
        answer = ask_agent(
            "stablecoin", agents["stablecoin"], pd.Timestamp("2026-11-07"), self.history()
        )
        self.assertGreater(answer.probability, 0.5)
        self.assertLessEqual(answer.probability, 0.6)

    def test_unavailable_stale_invalid_and_duplicate_evidence_abstains(self):
        for mode in (
            "future-release",
            "stale",
            "invalid",
            "duplicate",
            "unchanged-quote",
            "missing",
        ):
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
                self.assertIsNone(
                    ask_agent(
                        "stablecoin", stablecoin, pd.Timestamp("2026-11-07"), data
                    ).probability
                )

    def test_neutral_valid_supply_is_a_real_vote(self):
        data = self.history()
        for symbol in COINS.values():
            data["stablecoin"][symbol.lower() + "_market_cap_usd"] = 100
        result = stablecoin.forecast("2026-11-07", data)
        self.assertFalse(result["data_missing"])
        self.assertEqual(result["p_up"], 0.5)

    def test_future_rows_do_not_change_forecast(self):
        data = self.history()
        expected = stablecoin.forecast("2026-11-07", data)
        future = data["stablecoin"].tail(1).copy()
        future.index = pd.to_datetime(["2026-11-09"])
        future["usdt_price_usd"] = 0.5
        data["stablecoin"] = pd.concat([data["stablecoin"], future])
        self.assertEqual(expected, stablecoin.forecast("2026-11-07", data))

    def test_quality_buy_still_enforces_exposure_cap(self):
        trader = self.configured(PaperTrader())
        quote = LiquidityQuote("59990", "60010", "1", NOW)
        decision, trade, reason = execute_with_quality(
            trader,
            "BUY",
            "60000",
            quote=quote,
            requested_buy_fraction="0.24",
            timestamp=NOW,
            probability_up="0.5",
        )
        self.assertIsNotNone(trade)
        self.assertLessEqual(decision.projected_exposure, 0.25)
        self.assertLessEqual(trader.btc * 60000 / (trader.cash + trader.btc * 60000), 0.25)

    def test_unusable_liquidity_does_not_mutate_account(self):
        for quote in (
            None,
            LiquidityQuote(59990, 60010, 0.000001, NOW),
            LiquidityQuote(59990, 61000, 1, NOW),
            LiquidityQuote(59990, 60010, 1, NOW - timedelta(seconds=31)),
        ):
            trader = PaperTrader()
            _, trade, reason = execute_with_quality(
                trader, "BUY", 60000, quote=quote, timestamp=NOW
            )
            self.assertIsNone(trade)
            self.assertTrue(reason.startswith("DEFER"))
            self.assertEqual(trader.cash, 10000)
            self.assertEqual(trader.btc, 0)

    def test_quality_gate_never_blocks_required_liquidation(self):
        trader = self.configured(PaperTrader())
        trader.execute("BUY", 60000, buy_fraction="0.24", timestamp=NOW, probability_up="0.5")
        before = (trader.cash, trader.btc, tuple(trader.trades))
        # Price drift above cap cannot silently turn HOLD into a SELL.
        decision, trade, _ = execute_with_quality(
            trader, "HOLD", 100000, timestamp=NOW + timedelta(seconds=1), probability_up="0.5"
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertIsNone(trade)
        self.assertEqual((trader.cash, trader.btc, tuple(trader.trades)), before)
        # An explicit risk-reducing SELL does not require a BUY liquidity quote.
        decision, trade, _ = execute_with_quality(
            trader, "SELL", 100000, timestamp=NOW + timedelta(seconds=2), probability_up="0.5"
        )
        self.assertEqual(decision.action, "SELL")
        self.assertIsNotNone(trade)
        self.assertEqual(trader.btc, 0)

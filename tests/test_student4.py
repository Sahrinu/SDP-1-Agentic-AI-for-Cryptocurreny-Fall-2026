import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from student4 import CoinGeckoClient, DataError, PaperTrader, StablecoinAgent, StablecoinSnapshot
from student4.data import COINS
from student4.peg import derive_depeg_signal

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def payload(now=NOW, *, price=1.0, cap=1000000000):
    return {coin_id: {"usd": price, "usd_market_cap": cap,
                      "usd_24h_vol": 50000000, "last_updated_at": now.timestamp()}
            for coin_id in COINS}


def snapshot(now=NOW, *, price=1.0, cap=1000000000):
    return StablecoinSnapshot.from_payload(payload(now, price=price, cap=cap),
                                          observed_at=now, source="test")


class DataTests(unittest.TestCase):
    def test_parse_all_required_coins(self):
        data = snapshot()
        self.assertEqual({q.symbol for q in data.quotes}, {"USDT", "USDC", "DAI"})
        self.assertEqual(data.total_market_cap_usd, 3000000000)
        data.validate_freshness(NOW)

    def test_missing_coin_or_field_is_not_zero_filled(self):
        for missing in ("tether", "usd_market_cap"):
            raw = payload()
            if missing == "tether":
                del raw[missing]
            else:
                del raw["tether"][missing]
            with self.subTest(missing=missing), self.assertRaises(DataError):
                StablecoinSnapshot.from_payload(raw, observed_at=NOW, source="test")

    def test_invalid_market_values(self):
        for field in ("usd", "usd_market_cap", "usd_24h_vol", "last_updated_at"):
            for invalid in (None, float("nan"), float("inf"), -1, True, "1.0"):
                raw = payload()
                raw["tether"][field] = invalid
                with self.subTest(field=field, value=invalid), self.assertRaises(DataError):
                    StablecoinSnapshot.from_payload(raw, observed_at=NOW, source="test")

    def test_zero_price_and_cap_rejected_but_zero_volume_allowed(self):
        for field in ("usd", "usd_market_cap"):
            raw = payload()
            raw["tether"][field] = 0
            with self.assertRaises(DataError):
                StablecoinSnapshot.from_payload(raw, observed_at=NOW, source="test")
        raw["tether"][field] = 1
        raw["tether"]["usd_24h_vol"] = 0
        StablecoinSnapshot.from_payload(raw, observed_at=NOW, source="test")

    def test_stale_and_future_quotes_rejected(self):
        for delta in (timedelta(seconds=-901), timedelta(seconds=61)):
            with self.subTest(delta=delta), self.assertRaises(DataError):
                snapshot(NOW + delta).validate_freshness(NOW)

    def test_new_observation_cannot_hide_stale_provider_quote(self):
        stale = snapshot(NOW - timedelta(hours=1))
        with self.assertRaises(DataError):
            replace(stale, observed_at=NOW).validate_freshness(NOW)

    def test_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            snapshot().save(path)
            self.assertEqual(StablecoinSnapshot.load(path), snapshot())

    def test_invalid_snapshot_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            for invalid in ("not-json", "{}", "[]"):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(DataError):
                    StablecoinSnapshot.load(path)

    @patch.dict(os.environ, {}, clear=True)
    def test_live_requires_key(self):
        with self.assertRaisesRegex(DataError, "COINGECKO_DEMO_API_KEY"):
            CoinGeckoClient().fetch()

    @patch("student4.data.urlopen")
    def test_request_parameters_and_live_response(self, mocked_open):
        now = datetime.now(timezone.utc)
        mocked_open.return_value.__enter__.return_value = io.StringIO(json.dumps(payload(now)))
        result = CoinGeckoClient(api_key="test-key", timeout=7).fetch()
        request = mocked_open.call_args.args[0]
        params = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(set(params["ids"][0].split(",")), set(COINS))
        self.assertEqual(params["include_last_updated_at"], ["true"])
        self.assertEqual(params["include_market_cap"], ["true"])
        self.assertEqual(params["include_24hr_vol"], ["true"])
        self.assertEqual(mocked_open.call_args.kwargs["timeout"], 7)
        self.assertIn("test-key", request.headers.values())
        self.assertNotIn("test-key", request.full_url)
        self.assertEqual(result.source, "coingecko")

    @patch("student4.data.urlopen")
    def test_api_failures_are_explicit(self, mocked_open):
        for error in (HTTPError("https://example.invalid", 429, "rate limit", {}, None),
                      URLError("offline"), TimeoutError()):
            mocked_open.side_effect = error
            with self.subTest(error=error), self.assertRaises(DataError):
                CoinGeckoClient(api_key="test-key").fetch()

    @patch("student4.data.urlopen")
    def test_invalid_api_json(self, mocked_open):
        mocked_open.return_value.__enter__.return_value = io.StringIO("not-json")
        with self.assertRaises(DataError):
            CoinGeckoClient(api_key="test-key").fetch()


class PegTests(unittest.TestCase):
    def test_direction_and_exact_unrounded_deviation(self):
        below = derive_depeg_signal("0.995")
        self.assertEqual(below.raw_bps, Decimal("-50"))
        self.assertEqual(below.abs_raw_bps, Decimal("50"))
        self.assertEqual(below.direction, "below")
        self.assertEqual(derive_depeg_signal("1.02").direction, "above")
        self.assertEqual(derive_depeg_signal("0.995001").abs_raw_bps, Decimal("49.990000"))

    def test_invalid_prices_or_references(self):
        for invalid in (0, -1, None, True, "NaN", "Infinity"):
            self.assertIsNone(derive_depeg_signal(invalid))
            self.assertIsNone(derive_depeg_signal(1, invalid))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = StablecoinAgent()
        self.previous = snapshot(NOW - timedelta(days=1))

    def test_no_baseline_and_flat_liquidity_hold(self):
        self.assertEqual(self.agent.analyze(snapshot(), now=NOW).action, "HOLD")
        self.assertEqual(self.agent.analyze(snapshot(), self.previous, now=NOW).action, "HOLD")

    def test_growth_buy_and_contraction_sell(self):
        for cap, action, score in ((1010000000, "BUY", 1), (990000000, "SELL", -1)):
            with self.subTest(action=action):
                result = self.agent.analyze(snapshot(cap=cap), self.previous, now=NOW)
                self.assertEqual((result.action, result.score), (action, score))
                self.assertEqual(result.metrics["comparison_hours"], 24)
                self.assertEqual(result.to_dict()["agent"], "stablecoin")

    def test_thresholds_in_both_directions(self):
        for price, action in ((0.995001, "BUY"), (0.995000000001, "BUY"),
                              (0.995, "HOLD"), (1.005, "HOLD"),
                              (0.980001, "HOLD"), (0.980000000001, "HOLD"),
                              (0.98, "SELL"), (1.02, "SELL")):
            with self.subTest(price=price):
                result = self.agent.analyze(snapshot(price=price, cap=2000000000), self.previous, now=NOW)
                self.assertEqual(result.action, action)

    def test_one_coin_depeg_overrides_growth(self):
        raw = payload(cap=2000000000)
        raw["usd-coin"]["usd"] = 0.97
        current = StablecoinSnapshot.from_payload(raw, observed_at=NOW, source="test")
        result = self.agent.analyze(current, self.previous, now=NOW)
        self.assertEqual(result.action, "SELL")
        self.assertIn("USDC", result.reason)

    def test_stale_data_holds_even_if_depegged(self):
        result = self.agent.analyze(snapshot(NOW - timedelta(hours=1), price=0.9), now=NOW)
        self.assertEqual(result.action, "HOLD")
        self.assertIn("stale", result.reason)

    def test_invalid_baselines_hold(self):
        for previous in (snapshot(), snapshot(NOW + timedelta(days=1)),
                         replace(self.previous, source="other-source"),
                         replace(self.previous, observed_at=NOW - timedelta(hours=1)),
                         replace(self.previous, quotes=self.previous.quotes[:2])):
            with self.subTest(previous=previous):
                result = self.agent.analyze(snapshot(cap=2000000000), previous, now=NOW)
                self.assertEqual(result.action, "HOLD")

    def test_repeated_cached_quotes_do_not_create_growth_signal(self):
        current = replace(snapshot(cap=2000000000), observed_at=NOW + timedelta(minutes=1))
        result = self.agent.analyze(current, snapshot(), now=NOW + timedelta(minutes=1))
        self.assertEqual(result.action, "HOLD")
        self.assertIn("newer", result.reason)

    def test_invalid_thresholds(self):
        for values in ((0, 2, 1), (2, 1, 1), (0.5, 2, 0), (0.5, float("nan"), 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                StablecoinAgent(*values)


class PaperTradingTests(unittest.TestCase):
    def test_exact_no_cost_round_trip(self):
        trader = PaperTrader("1000", fee_rate=0, slippage_bps=0)
        trader.execute("BUY", "100", timestamp=NOW)
        self.assertEqual(trader.btc, Decimal("10"))
        self.assertEqual(trader.cash, 0)
        self.assertEqual(Decimal(trader.summary("110")["unrealized_pnl_usd"]), 100)
        trader.execute("SELL", "110", timestamp=NOW + timedelta(hours=1))
        self.assertEqual(trader.cash, 1100)
        self.assertEqual(trader.btc, 0)
        self.assertEqual(trader.cost_basis, 0)
        self.assertEqual(trader.realized_pnl, 100)
        self.assertEqual(Decimal(trader.summary("110")["total_pnl_usd"]), 100)

    def test_fees_slippage_and_pnl_reconcile(self):
        trader = PaperTrader("1000", fee_rate="0.01", slippage_bps="100")
        buy = trader.execute("BUY", "100", timestamp=NOW)
        self.assertEqual(Decimal(buy.execution_price_usd), 101)
        self.assertGreaterEqual(trader.cash, 0)
        self.assertEqual(trader.cash + trader.cost_basis, trader.initial_cash)
        sell = trader.execute("SELL", "100", timestamp=NOW + timedelta(hours=1))
        self.assertEqual(Decimal(sell.execution_price_usd), 99)
        self.assertLess(trader.realized_pnl, 0)
        self.assertEqual(trader.cash - trader.initial_cash, trader.realized_pnl)
        self.assertEqual(trader.total_fees, Decimal(buy.fee_usd) + Decimal(sell.fee_usd))
        self.assertEqual(Decimal(trader.summary("100")["unrealized_pnl_usd"]), 0)

    def test_sizing_repeated_buys_and_total_cost_basis(self):
        trader = PaperTrader("1000", fee_rate=0, slippage_bps=0)
        trader.execute("BUY", "100", buy_fraction="0.25", timestamp=NOW)
        self.assertEqual(trader.cash, 750)
        trader.execute("BUY", "150", timestamp=NOW + timedelta(hours=1))
        self.assertEqual(trader.btc, Decimal("7.5"))
        self.assertEqual(trader.cost_basis, 1000)
        summary = trader.summary("120")
        self.assertEqual(Decimal(summary["total_pnl_usd"]), -100)
        self.assertEqual(Decimal(summary["total_pnl_usd"]),
                         Decimal(summary["realized_pnl_usd"]) + Decimal(summary["unrealized_pnl_usd"]))

    def test_hold_no_position_sell_and_insufficient_cash_are_noops(self):
        trader = PaperTrader(fee_rate=0, slippage_bps=0)
        self.assertIsNone(trader.execute("HOLD", "100"))
        self.assertIsNone(trader.execute("SELL", "100"))
        trader.execute("BUY", "100")
        self.assertIsNone(trader.execute("BUY", "100"))
        self.assertEqual(len(trader.trades), 1)

    def test_invalid_orders_leave_account_unchanged(self):
        trader = PaperTrader()
        before = trader.summary(100)
        for action, price, fraction in (("WAIT", 100, 1), ("BUY", 0, 1), ("BUY", "NaN", 1),
                                        ("BUY", True, 1), ("BUY", 100, 0), ("BUY", 100, "1.1")):
            with self.subTest(action=action, price=price, fraction=fraction), self.assertRaises(ValueError):
                trader.execute(action, price, buy_fraction=fraction)
            self.assertEqual(trader.summary(100), before)

    def test_trade_timestamps_must_be_aware_and_ordered(self):
        trader = PaperTrader()
        with self.assertRaises(ValueError):
            trader.execute("BUY", 100, timestamp=NOW.replace(tzinfo=None))
        trader.execute("BUY", 100, timestamp=NOW)
        with self.assertRaises(ValueError):
            trader.execute("SELL", 100, timestamp=NOW - timedelta(seconds=1))
        self.assertEqual(len(trader.trades), 1)

    def test_invalid_account_settings(self):
        for kwargs in ({"initial_cash": 0}, {"fee_rate": 1}, {"slippage_bps": 10000},
                       {"fee_rate": "NaN"}, {"initial_cash": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PaperTrader(**kwargs)

    def test_ledger_export(self):
        trader = PaperTrader()
        trader.execute("BUY", 100, timestamp=NOW)
        with tempfile.TemporaryDirectory() as folder:
            trader.export(folder, 100)
            with (Path(folder) / "trades.csv").open(newline="", encoding="utf-8") as handle:
                ledger = list(csv.DictReader(handle))
            self.assertEqual(ledger[0]["action"], "BUY")
            self.assertEqual(Decimal(ledger[0]["btc_after"]), trader.btc)
            summary = json.loads((Path(folder) / "portfolio.json").read_text())
            self.assertEqual(summary, trader.summary(100))


class CLITests(unittest.TestCase):
    def test_demo_runs_without_network_or_key(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-m", "student4", "demo", "--output", folder],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            signals = json.loads((Path(folder) / "signals.json").read_text())
            self.assertEqual([s["action"] for s in signals], ["HOLD", "BUY", "HOLD", "SELL"])
            portfolio = json.loads((Path(folder) / "portfolio.json").read_text())
            self.assertEqual(portfolio["trade_count"], 2)
            self.assertEqual(Decimal(portfolio["btc"]), 0)
            self.assertEqual(len(list((Path(folder) / "snapshots").glob("*.json"))), 4)
            self.assertIn("synthetic", result.stdout)

    def test_collect_without_key_has_clear_error_and_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "current.json"
            env = {k: v for k, v in os.environ.items() if k != "COINGECKO_DEMO_API_KEY"}
            result = subprocess.run([sys.executable, "-m", "student4", "collect", "--output", str(target)],
                                    capture_output=True, text=True, env=env, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("COINGECKO_DEMO_API_KEY", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

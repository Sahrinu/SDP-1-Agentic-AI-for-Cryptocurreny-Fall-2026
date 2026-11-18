"""Persistent live settlement: coins learned from settled live forecasts."""
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from exchange.ledger import LiveLedger

T0 = datetime(2026, 11, 16, 10, 0, 30, tzinfo=timezone.utc)


def live_forecast(fid, created, probabilities, *, price=90000.0, abstain=()):
    agents = [{"agent": a, "role": a, "p_up": p, "audit": {"passed": True}}
              for a, p in probabilities.items()]
    agents += [{"agent": a, "role": a, "p_up": None, "audit": {"passed": False}} for a in abstain]
    return {"id": fid, "mode": "live", "created_at": created.isoformat(),
            "contract": {"prediction_time": created.isoformat(),
                         "target_time": (created + timedelta(hours=24)).isoformat()},
            "quote": {"price": price}, "agents": agents, "methods": {"exchange": 0.6}}


def quote_at(target, price):
    # last completed one-minute bar before the target time (Binance close_time style)
    close = target.replace(second=0, microsecond=0) - timedelta(milliseconds=1)
    return {"price": price, "bar_close_time": close.isoformat()}


class LiveLedgerTests(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "ledger.sqlite3"
        self.ledger = LiveLedger(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_not_due_before_target(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7, "bear": 0.3}))
        self.assertEqual(self.ledger.due(T0 + timedelta(hours=23)), [])
        with self.assertRaises(ValueError):
            self.ledger.settle("a", quote_at(T0 + timedelta(hours=24), 95000), now=T0 + timedelta(hours=23))

    def test_right_agent_gains_wrong_agent_loses_and_coins_persist(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7, "bear": 0.3, "unsure": 0.5}))
        target = T0 + timedelta(hours=24)
        result = self.ledger.settle("a", quote_at(target, 95000), now=target + timedelta(minutes=5))
        self.assertTrue(result["settled"])
        self.assertEqual(result["went_up"], 1)
        coins = LiveLedger(self.path).coins()          # reopened: saved on disk
        self.assertAlmostEqual(coins["bull"], 104.0)    # 10% of 100 x 0.4 confidence
        self.assertAlmostEqual(coins["bear"], 96.0)
        self.assertAlmostEqual(coins["unsure"], 100.0)  # 50% bets nothing

    def test_next_forecast_uses_learned_coins(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.9, "bear": 0.1}))
        target = T0 + timedelta(hours=24)
        self.ledger.settle("a", quote_at(target, 95000), now=target)
        ex = self.ledger.exchange_for(["bull", "bear", "new_agent"])
        self.assertGreater(ex.coins["bull"], ex.coins["bear"])
        self.assertEqual(ex.coins["new_agent"], 100.0)  # never settled: starting coins

    def test_tie_counts_as_down(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}, price=90000))
        target = T0 + timedelta(hours=24)
        result = self.ledger.settle("a", quote_at(target, 90000), now=target)
        self.assertEqual(result["went_up"], 0)
        self.assertLess(self.ledger.coins()["bull"], 100)

    def test_settle_is_idempotent(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}))
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}))   # duplicate ignored
        target = T0 + timedelta(hours=24)
        first = self.ledger.settle("a", quote_at(target, 95000), now=target)
        again = self.ledger.settle("a", quote_at(target, 10), now=target)
        self.assertEqual(first, again)
        self.assertAlmostEqual(self.ledger.coins()["bull"], 104.0)

    def test_oldest_first(self):
        self.ledger.record(live_forecast("old", T0, {"bull": 0.7}))
        self.ledger.record(live_forecast("new", T0 + timedelta(hours=1), {"bull": 0.7}))
        later = T0 + timedelta(hours=30)
        with self.assertRaises(ValueError):
            self.ledger.settle("new", quote_at(T0 + timedelta(hours=25), 95000), now=later)
        outcome = self.ledger.settle_due(lambda target: quote_at(target, 95000), now=later)
        self.assertEqual([r["id"] for r in outcome["settled"]], ["old", "new"])
        self.assertIsNone(outcome["problem"])
        self.assertEqual(self.ledger.summary()["pending"], 0)

    def test_settle_due_stops_at_price_failure(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}))
        def broken(target):
            raise ConnectionError("no network")
        outcome = self.ledger.settle_due(broken, now=T0 + timedelta(hours=25))
        self.assertEqual(outcome["settled"], [])
        self.assertEqual(outcome["problem"]["forecast_id"], "a")
        self.assertEqual(self.ledger.coins(), {})        # nothing changed

    def test_rejects_wrong_target_price_time(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}))
        target = T0 + timedelta(hours=24)
        late_bar = {"price": 95000, "bar_close_time": (target + timedelta(minutes=1)).isoformat()}
        old_bar = {"price": 95000, "bar_close_time": (target - timedelta(minutes=10)).isoformat()}
        for bad in (late_bar, old_bar, {"price": -1, "bar_close_time": quote_at(target, 1)["bar_close_time"]}):
            with self.assertRaises(ValueError):
                self.ledger.settle("a", bad, now=target + timedelta(hours=1))
        self.assertEqual(self.ledger.coins(), {})

    def test_abstaining_agents_do_not_bet(self):
        self.ledger.record(live_forecast("a", T0, {"bull": 0.7}, abstain=("news",)))
        target = T0 + timedelta(hours=24)
        result = self.ledger.settle("a", quote_at(target, 95000), now=target)
        self.assertEqual([b["agent"] for b in result["bets"]], ["bull"])
        self.assertNotIn("news", self.ledger.coins())

    def test_only_live_24h_forecasts(self):
        bad = live_forecast("a", T0, {"bull": 0.7})
        bad["mode"] = "replay"
        with self.assertRaises(ValueError):
            self.ledger.record(bad)
        bad = live_forecast("b", T0, {"bull": 0.7})
        bad["contract"]["target_time"] = (T0 + timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError):
            self.ledger.record(bad)


class TargetQuoteTests(unittest.TestCase):

    def test_uses_last_completed_bar_at_or_before_target(self):
        from market_data.live import target_quote
        target = datetime(2026, 11, 10, 12, 0, 30, tzinfo=timezone.utc)
        ms = int(target.timestamp() * 1000)
        minute = 60_000
        start = ms - 30_000                               # bar opened at 12:00:00
        bars = [[start - 2 * minute, "0", "0", "0", "100", "0", start - minute - 1],
                [start - minute, "0", "0", "0", "101", "0", start - 1],
                [start, "0", "0", "0", "999", "0", start + minute - 1]]   # not finished at target
        seen = {}
        def get(url):
            seen["url"] = url
            return bars
        q = target_quote(target, get=get)
        self.assertEqual(q["price"], 101.0)
        self.assertIn("endTime=" + str(ms), seen["url"])
        with self.assertRaises(ValueError):
            target_quote(datetime.now(timezone.utc) + timedelta(hours=1), get=get)


class DashboardLedgerTests(unittest.TestCase):

    def test_forecast_reports_learning_basis(self):
        from dashboard.service import forecast_snapshot
        now = datetime.now(timezone.utc).replace(microsecond=0)
        quote = {"price": 90000.0, "bar_close_time": (now - timedelta(seconds=30)).isoformat(),
                 "retrieved_at": (now - timedelta(seconds=5)).isoformat()}
        with tempfile.TemporaryDirectory() as folder:
            ledger = LiveLedger(Path(folder) / "ledger.sqlite3")
            result = forecast_snapshot({}, quote, decision_time=now, ledger=ledger)
            self.assertIn("settled live forecasts", result["coins_basis"])
            ledger.record(result)
            self.assertEqual(ledger.summary()["pending"], 1)


if __name__ == "__main__":
    unittest.main()

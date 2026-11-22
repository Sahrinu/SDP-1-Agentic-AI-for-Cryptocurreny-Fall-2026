"""Team-level fake-news test on a saved live forecast (no real model calls)."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from experiments.live_attack import run, team_forecast

PREDICTION = "2026-11-18T14:40:53+00:00"


def fake_news_agent(attacked_p=0.9, clean_p=0.45, abstain_when_attacked=False):
    """Stands in for the Gemini News agent: reacts only to the injected article."""
    def ask(view, day, prediction_time):
        attacked = any(str(a.get("evidence_id", "")).startswith("local-attack-")
                       for a in view["student2_news_articles"])
        if attacked and abstain_when_attacked:
            return None, ["abstained"]
        return (attacked_p if attacked else clean_p), ["fake agent evidence"]
    return ask


def saved_forecast(folder, *, news_coins=100.0):
    news = Path(folder) / "news.json"
    news.write_text(json.dumps({"observed_at": "2026-11-18T14:40:00+00:00",
                                "articles": [{"evidence_id": "real-1", "title": "BTC steady",
                                              "publication_timestamp": "2026-11-18T13:00:00+00:00"}]}))
    others = {"chart": 0.40, "network": 0.55, "miner": 0.51, "crash": 0.39, "long_term": 0.56, "mood": 0.52}
    agents = [{"agent": n, "role": n, "p_up": p, "evidence": ["x"], "audit": {"passed": True}}
              for n, p in others.items()]
    agents.append({"agent": "News Specialist", "role": "News", "p_up": 0.45, "evidence": ["x"],
                   "audit": {"passed": True}})
    agents.append({"agent": "contrarian", "role": "Contrarian", "p_up": 0.5, "evidence": ["x"],
                   "audit": {"passed": True}})
    agents.append({"agent": "etf", "role": "ETF", "p_up": None, "evidence": ["no data"],
                   "audit": {"passed": False}})
    coins = {n: 100.0 for n in others}
    coins.update({"News Specialist": news_coins, "contrarian": 100.0})
    forecast = {"id": "abc123", "mode": "live", "created_at": PREDICTION,
                "contract": {"prediction_time": PREDICTION, "target_time": "2026-11-19T14:40:53+00:00"},
                "agents": agents, "coins": coins, "news_capture": str(news)}
    path = Path(folder) / "forecast.json"
    path.write_text(json.dumps(forecast))
    return path


class LiveAttackTests(unittest.TestCase):

    def test_section_5_6_runs_100_pairs_per_scenario(self):
        from experiments.news_robustness import select_attacks

        with tempfile.TemporaryDirectory() as folder:
            path = saved_forecast(folder)
            scenarios = select_attacks("section_5_6")
            seen = []

            def ask(view, day, prediction_time):
                articles = view["student2_news_articles"]
                if "REPLAY-HIDDEN-TEXT-MARKER" in articles[0].get("text", ""):
                    self.assertEqual(len(articles), 1)
                    self.assertEqual(articles[0]["title"], "BTC steady")
                    seen.append("hidden_text")
                elif len(articles) > 1:
                    injected = articles[-1]
                    if injected["evidence_id"] == "local-attack-poisoned_memory":
                        self.assertEqual(injected["source_type"], "simulated_prior_session_note")
                    seen.append(injected["evidence_id"].removeprefix("local-attack-"))
                return 0.5, []

            rows, _, _ = run(path, ask, attacks=scenarios, repeats=100,
                             out_root=Path(folder) / "attacks")
            self.assertEqual(len(rows), 4 * 100 * 3)
            self.assertEqual({scenario: seen.count(scenario) for scenario in scenarios},
                             {scenario: 100 for scenario in scenarios})
            self.assertTrue(all(row["comparable"] for row in rows))

    def test_shift_for_every_method_and_csv_appended(self):
        with tempfile.TemporaryDirectory() as folder:
            path = saved_forecast(folder)
            rows, summary, out = run(path, fake_news_agent(), attacks=("bearish_claim", "prompt_injection"),
                                     repeats=2, out_root=Path(folder) / "attacks")
            self.assertEqual(len(rows), 2 * 2 * 3)
            self.assertTrue(all(r["comparable"] for r in rows))
            by = {s["method"]: s for s in summary}
            self.assertAlmostEqual(by["average"]["mean_shift"], (0.9 - 0.45) / 8, places=6)
            self.assertGreater(by["vote"]["mean_shift"], 0)
            saved = pd.read_csv(Path(folder) / "attacks" / "all_shifts.csv")
            self.assertEqual(len(saved), 12)
            run(path, fake_news_agent(), repeats=1, out_root=Path(folder) / "attacks")
            self.assertEqual(len(pd.read_csv(Path(folder) / "attacks" / "all_shifts.csv")), 15)
            self.assertTrue((out / "details.json").exists())

    def test_exchange_uses_saved_coins(self):
        with tempfile.TemporaryDirectory() as folder:
            trusted = run(saved_forecast(folder, news_coins=100.0), fake_news_agent(), repeats=1,
                          out_root=Path(folder) / "a")[1]
            distrusted = run(saved_forecast(folder, news_coins=40.0), fake_news_agent(), repeats=1,
                             out_root=Path(folder) / "b")[1]
            ex = lambda s: {x["method"]: x for x in s}["exchange"]["mean_shift"]
            self.assertLess(ex(distrusted), ex(trusted))   # a News agent with fewer coins moves the team less

    def test_abstaining_news_is_not_comparable(self):
        with tempfile.TemporaryDirectory() as folder:
            rows, summary, _ = run(saved_forecast(folder), fake_news_agent(abstain_when_attacked=True),
                                   repeats=1, out_root=Path(folder) / "attacks")
            self.assertFalse(any(r["comparable"] for r in rows))
            self.assertTrue(all(s["mean_shift"] is None for s in summary))

    def test_contrarian_is_recomputed_from_changed_peers(self):
        first = {"a": 0.8, "b": 0.8, "c": 0.8, "d": 0.8}
        _, used = team_forecast(first, {}, "contrarian", "2026-11-18")
        self.assertLess(used["contrarian"], 0.5)          # strong consensus -> leans the other way
        _, used = team_forecast({"a": 0.5, "b": 0.6, "c": 0.4}, {}, "contrarian", "2026-11-18")
        self.assertEqual(used["contrarian"], 0.5)

    def test_rejects_non_live_forecast(self):
        with tempfile.TemporaryDirectory() as folder:
            path = saved_forecast(folder)
            data = json.loads(path.read_text())
            data["mode"] = "replay"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                run(path, fake_news_agent(), out_root=Path(folder) / "attacks")

    def test_reused_news_found_in_collect_snapshot(self):
        from experiments.live_attack import news_snapshot
        with tempfile.TemporaryDirectory() as folder:
            forecast = json.loads(saved_forecast(folder).read_text())
            forecast["news_capture"] = None             # forecast reused the Refresh news
            snapshots = Path(folder) / "snapshots"
            for name, observed in (("old", "2026-11-18T14:30:00+00:00"),       # too early
                                   ("good", "2026-11-18T14:40:42+00:00"),      # 11 s before
                                   ("zlate", "2026-11-18T14:45:00+00:00")):    # after decision
                d = snapshots / name
                d.mkdir(parents=True)
                (d / "student2_news_articles.json").write_text(json.dumps([{"evidence_id": name}]))
                (d / "student2_news_observed_at.json").write_text(json.dumps(observed))
            articles, observed = news_snapshot(forecast, snapshots)
            self.assertEqual(articles, [{"evidence_id": "good"}])
            (snapshots / "good" / "student2_news_observed_at.json").write_text(
                json.dumps("2026-11-18T14:20:00+00:00"))
            with self.assertRaises(ValueError):
                news_snapshot(forecast, snapshots)

    def test_dashboard_snapshot_folder_as_capture(self):
        from experiments.live_attack import news_snapshot
        with tempfile.TemporaryDirectory() as folder:
            forecast = json.loads(saved_forecast(folder).read_text())
            d = Path(folder) / "snap"
            d.mkdir()
            (d / "student2_news_articles.json").write_text(json.dumps([{"evidence_id": "x"}]))
            (d / "student2_news_observed_at.json").write_text(json.dumps("2026-11-18T14:40:00+00:00"))
            forecast["news_capture"] = str(d)
            self.assertEqual(news_snapshot(forecast)[0], [{"evidence_id": "x"}])

    def test_final_table_includes_live_attacks(self):
        from experiments.report import build
        from experiments.run import ROOT, run as replay
        with tempfile.TemporaryDirectory() as folder:
            run(saved_forecast(folder), fake_news_agent(), repeats=2, out_root=Path(folder) / "attacks")
            replay("2026-10-01", "2026-10-05", 0, ROOT / "tests/fake_data", ROOT / "tests/fake_agents",
                   Path(folder) / "replay", synthetic=True)
            table, notes = build(Path(folder) / "replay",
                                 live_attacks=Path(folder) / "attacks" / "all_shifts.csv")
            row = table.set_index("method").loc["Simple average"]
            self.assertEqual(row["live_attack_pairs"], 2)
            self.assertEqual(row["live_forecasts_attacked"], 1)
            self.assertGreater(row["live_attack_mean_shift"], 0)


if __name__ == "__main__":
    unittest.main()

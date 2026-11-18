"""Final results table built from saved replay outputs."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from experiments.report import build, write
from experiments.run import ROOT, run
from tests.test_run_debate import FakeDebateModel


class FinalReportTests(unittest.TestCase):

    def test_one_row_per_method_with_intervals_and_costs(self):
        with tempfile.TemporaryDirectory() as folder:
            run("2026-10-01", "2026-10-20", 3, ROOT / "tests/fake_data", ROOT / "tests/fake_agents",
                folder, synthetic=True, debate_client=FakeDebateModel())
            table, notes = build(folder, price_in=0.25, price_out=1.50)
            write(table, notes, folder)
            self.assertEqual(list(table["method"]),
                             ["Agent Exchange", "Majority vote", "Simple average", "Debate (Bull/Bear/Judge)"])
            for column in ("brier", "brier_ci95_low", "brier_ci95_high", "mean_attack_shift"):
                self.assertTrue(table[column].notna().all(), column)
            debate = table.set_index("method").loc["Debate (Bull/Bear/Judge)"]
            self.assertEqual(debate["model_calls_per_decision"], 3)
            self.assertGreater(debate["usd_per_decision"], 0)
            self.assertEqual(table.set_index("method").loc["Agent Exchange", "usd_per_decision"], 0)
            self.assertTrue((Path(folder) / "final_results.md").exists())
            saved = pd.read_csv(Path(folder) / "final_results.csv")
            self.assertEqual(len(saved), 4)

    def test_without_prices_costs_stay_in_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            run("2026-10-01", "2026-10-05", 0, ROOT / "tests/fake_data", ROOT / "tests/fake_agents",
                folder, synthetic=True)
            table, notes = build(folder)
            self.assertNotIn("usd_per_decision", table)
            self.assertTrue(any("--price-in" in n for n in notes))


if __name__ == "__main__":
    unittest.main()

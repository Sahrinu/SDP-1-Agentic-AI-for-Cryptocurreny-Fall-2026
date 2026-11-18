"""Debate as a fourth comparison method in the replay runner (no real model calls)."""
import json
import tempfile
import unittest
from pathlib import Path

from experiments.run import ROOT, run


class FakeDebateModel:
    """Answers like the Gemini bridge: JSON text, records usage per call."""

    def __init__(self, judge_p=0.6, broken=False):
        self.judge_p, self.broken, self.usage = judge_p, broken, []

    def __call__(self, prompt):
        self.usage.append({"model": "fake", "input_tokens": len(prompt) // 4, "output_tokens": 20})
        if self.broken:
            return "not json"
        reply = {"argument": "Cites the supplied reports only.", "evidence_ids": ["0"]}
        if "For Judge" in prompt:
            reply["p_up"] = self.judge_p
        return json.dumps(reply)


class RunDebateTests(unittest.TestCase):

    def replay(self, model, folder, attack_days=2):
        return run("2026-10-01", "2026-10-10", attack_days, ROOT / "tests/fake_data",
                   ROOT / "tests/fake_agents", folder, synthetic=True, debate_client=model)

    def test_debate_is_a_fourth_method(self):
        model = FakeDebateModel(judge_p=0.6)
        with tempfile.TemporaryDirectory() as folder:
            results, coins, attacks, summary = self.replay(model, folder)
            self.assertIn("debate", results)
            self.assertTrue((results["debate"] == 0.6).all())
            self.assertEqual(list(summary["method"]), ["exchange", "vote", "average", "debate"])
            self.assertEqual(set(attacks["method"]), {"exchange", "vote", "average", "debate"})
            # 10 clean days + 2 attack days, 3 calls each
            self.assertEqual(len(model.usage), (10 + 2) * 3)
            usage = json.loads((Path(folder) / "model_usage.json").read_text())
            self.assertEqual(usage["debate_calls"], 36)
            uncertainty = json.loads((Path(folder) / "uncertainty.json").read_text())
            self.assertIn("debate", uncertainty)

    def test_invalid_debate_output_is_missing_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            results, _, attacks, summary = self.replay(FakeDebateModel(broken=True), folder, attack_days=1)
            self.assertTrue(results["debate"].isna().all())
            self.assertTrue(results["exchange"].notna().all())
            row = summary.set_index("method").loc["debate"]
            self.assertEqual(row["evaluated_days"], 0)
            log = json.loads((Path(folder) / "debate_log.json").read_text())
            self.assertTrue(all(entry["status"] == "failed" for entry in log))

    def test_without_debate_the_runner_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            results, _, _, summary = run("2026-10-01", "2026-10-05", 0, ROOT / "tests/fake_data",
                                         ROOT / "tests/fake_agents", folder, synthetic=True)
            self.assertNotIn("debate", results)
            self.assertEqual(list(summary["method"]), ["exchange", "vote", "average"])
            self.assertFalse((Path(folder) / "model_usage.json").exists())


if __name__ == "__main__":
    unittest.main()

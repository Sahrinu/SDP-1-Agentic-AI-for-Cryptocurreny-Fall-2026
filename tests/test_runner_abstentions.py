"""Offline runner tests with real exchange code and explicitly mocked agents."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('runner_under_test', ROOT / 'experiments/run.py')
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

DAY = pd.Timestamp('2026-11-07')
NEXT = pd.Timestamp('2026-11-08')


def agent(p=None, missing=False):
    return SimpleNamespace(forecast=lambda day, data: {'p_up': p, 'data_missing': missing})


def configuration():
    c = dict(prediction_time='2026-11-07T17:00:00Z', target_time='2026-11-08T17:00:00Z',
             price_source='SYNTHETIC_TEST_FEED', reference_price_definition='synthetic_close', tie_definition='down')
    settlement = {k: c[k] for k in ('price_source', 'reference_price_definition', 'tie_definition')}
    settlement['close_times'] = {'2026-11-07': c['prediction_time'], '2026-11-08': c['target_time']}
    return {'student2_forecast': c}, settlement


class RunnerTests(unittest.TestCase):
    def run_case(self, agents, attack_days=0, *, dates=None, closes=None, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            pd.DataFrame({'date': dates or ['2026-11-07', '2026-11-08'],
                          'close': closes or [100, 110]}).to_csv(root / 'data/btc_prices.csv', index=False)
            with patch.object(runner, 'load_agents', return_value=agents), contextlib.redirect_stdout(io.StringIO()):
                result = runner.run('2026-11-07', '2026-11-08', attack_days,
                                    root / 'data', root / 'agents', root / 'output', **kwargs)
            # All artifacts must be parseable, including an attack CSV with no rows.
            for name in ('forecasts', 'coins', 'attack', 'summary'):
                pd.read_csv(root / 'output' / (name + '.csv'))
            return result

    def test_invalid_outputs_abstain(self):
        for value in (None, True, -0.1, 1.1, float('inf'), float('nan'), 'garbage'):
            with self.subTest(value=value):
                self.assertIsNone(runner.ask_agent('a', agent(value), DAY, {}).probability)
        for response in ([], {}, {'p_up': .8, 'data_missing': True},
                         {'p_up': .8, 'data_status': 'insufficient_data'}):
            self.assertIsNone(runner.ask_agent('a', SimpleNamespace(forecast=lambda *a: response), DAY, {}).probability)

    def test_exception_abstains_without_exposing_message(self):
        def fail(*args):
            raise RuntimeError('secret-key')
        result = runner.ask_agent('a', SimpleNamespace(forecast=fail), DAY, {})
        self.assertIsNone(result.probability)
        self.assertNotIn('secret-key', result.reason)

    def test_real_neutral_retained(self):
        self.assertEqual(runner.ask_agent('a', agent(.5), DAY, {}).probability, .5)
        results, coins, _, summary = self.run_case({'neutral': agent(.5)})
        self.assertEqual(results.iloc[0]['average'], .5)
        self.assertEqual(coins.iloc[0]['coins_neutral'], 100)
        self.assertTrue((summary.evaluated_days == 1).all())

    def test_original_probability_clipping_preserved(self):
        self.assertEqual(runner.ask_agent('a', agent(0), DAY, {}).probability, .05)
        self.assertEqual(runner.ask_agent('a', agent(1), DAY, {}).probability, .95)

    def test_missing_agent_excluded_and_coins_unchanged(self):
        results, coins, _, summary = self.run_case({'bull': agent(.8), 'missing': agent(None, True)})
        self.assertEqual(results.iloc[0]['average'], .8)
        self.assertTrue(pd.isna(results.iloc[0]['p_missing']))
        self.assertEqual(coins.iloc[0]['coins_missing'], 100)
        self.assertEqual(coins.iloc[0]['weight_missing'], 0)
        self.assertEqual(coins.iloc[0]['coins_bull'], 106)
        self.assertFalse(coins.iloc[0]['cap_feasible'])
        self.assertIn('missing', json.loads(results.iloc[0]['abstentions']))
        self.assertEqual(summary.set_index('method').loc['average', 'brier'], .04)

    def test_all_abstain_no_team_or_score_or_coin_change(self):
        results, coins, _, summary = self.run_case({'missing': agent(None, True)})
        self.assertTrue(results[runner.METHODS].isna().all().all())
        self.assertEqual(coins.iloc[0]['coins_missing'], 100)
        self.assertTrue((summary.evaluated_days == 0).all())
        self.assertTrue(summary.brier.isna().all())
        self.assertTrue(summary.direction_right.isna().all())

    def test_scoring_uses_only_available_days(self):
        def forecast(day, data):
            return {'p_up': .8 if day == '2026-11-07' else None}
        _, _, _, summary = self.run_case({'a': SimpleNamespace(forecast=forecast)},
            dates=['2026-11-07', '2026-11-08', '2026-11-09'], closes=[100, 110, 90])
        row = summary.set_index('method').loc['average']
        self.assertEqual(row.evaluated_days, 1)
        self.assertEqual(row.missing_days, 1)
        self.assertEqual(row.brier, .04)
        self.assertEqual(row.direction_right, 1)

    def test_attack_dropout_not_claimed_as_robustness(self):
        news = SimpleNamespace(forecast=lambda day, data: {'p_up': None if data['fake_headline'] else .8})
        _, coins, attacks, summary = self.run_case({'news': news, 'other': agent(.4)}, 1)
        self.assertFalse(attacks.comparable.any())
        self.assertTrue(attacks['shift'].isna().all())
        self.assertTrue((summary.comparable_attack_days == 0).all())
        self.assertEqual(coins.iloc[0]['coins_news'], 106)  # clean run alone settles

    def test_news_missing_in_both_runs_not_scored_as_resistance(self):
        _, _, attacks, summary = self.run_case({'news': agent(None, True), 'other': agent(.6)}, 1)
        self.assertFalse(attacks.comparable.any())
        self.assertTrue(attacks['shift'].isna().all())
        self.assertTrue((summary.comparable_attack_days == 0).all())

    def test_attack_with_all_abstaining_safe(self):
        _, _, attacks, _ = self.run_case({'news': agent(None, True)}, 1)
        self.assertTrue(attacks.clean.isna().all())
        self.assertTrue(attacks.attacked.isna().all())
        self.assertFalse(attacks.comparable.any())

    def test_comparable_attack_shift_computed(self):
        news = SimpleNamespace(forecast=lambda day, data: {'p_up': .2 if data['fake_headline'] else .8})
        _, _, attacks, _ = self.run_case({'news': news}, 1)
        self.assertTrue(attacks.comparable.all())
        self.assertAlmostEqual(attacks.set_index('method').loc['average', 'shift'], .6)

    def test_future_csv_rows_hidden(self):
        seen = []
        def forecast(day, data):
            seen.append(data['btc_prices'].index.max())
            return {'p_up': .6}
        self.run_case({'a': SimpleNamespace(forecast=forecast)})
        self.assertEqual(seen, [DAY])

    def test_contract_exact_match(self):
        snapshot, settlement = configuration()
        self.assertIsNone(runner.student2_window_error(DAY, NEXT, snapshot, settlement))

    def test_contract_mismatches_rejected(self):
        for field, value in [('target_time', '2026-11-08T18:00:00Z'),
                             ('prediction_time', '2026-11-07T17:00:00'),
                             ('price_source', 'WRONG'), ('reference_price_definition', 'WRONG'),
                             ('tie_definition', 'split')]:
            snapshot, settlement = configuration()
            snapshot['student2_forecast'][field] = value
            self.assertIsNotNone(runner.student2_window_error(DAY, NEXT, snapshot, settlement))
        snapshot, settlement = configuration()
        self.assertIsNotNone(runner.student2_window_error(DAY, pd.Timestamp('2026-11-09'), snapshot, settlement))
        self.assertIsNotNone(runner.student2_window_error(DAY, NEXT, {}, {}))

    def test_mismatch_prevents_specialist_call(self):
        def forbidden(*a):
            self.fail('Specialist must not run with an undefined settlement contract')
        results, _, _, _ = self.run_case({'legacy': agent(.6)},
            student2_adapters={'News Specialist': SimpleNamespace(forecast=forbidden)})
        self.assertTrue(pd.isna(results.iloc[0]['p_News Specialist']))
        self.assertEqual(results.iloc[0]['average'], .6)

    def test_explicit_matching_registration_uses_day_snapshot(self):
        snapshot, settlement = configuration()
        snapshot['student2_news_articles'] = [{'title': 'SYNTHETIC TEST INPUT'}]
        snapshot['btc_prices'] = 'must not override prices'
        seen = []
        def forecast(day, view):
            seen.append(view['student2_news_articles'])
            self.assertIsInstance(view['btc_prices'], pd.DataFrame)
            return {'p_up': .7}
        results, _, _, _ = self.run_case({}, student2_adapters={'News Specialist': SimpleNamespace(forecast=forecast)},
            student2_inputs={'2026-11-07': snapshot}, settlement_contract=settlement)
        self.assertEqual(results.iloc[0]['average'], .7)
        self.assertEqual(len(seen), 1)

    def test_duplicate_agent_names_rejected(self):
        with self.assertRaises(ValueError):
            self.run_case({'same': agent(.5)}, student2_adapters={'same': agent(.6)})

    def test_student2_betting_remains_unavailable_even_with_matching_configuration(self):
        snapshot, settlement = configuration()

        def forbidden(*args):
            self.fail('Betting Odds must not run without compatible verified live evidence')

        for name, adapter in (
            ('Betting Odds Specialist', SimpleNamespace(forecast=forbidden)),
            ('renamed betting', SimpleNamespace(kind='betting_odds', forecast=forbidden)),
            ('news betting', SimpleNamespace(kind='betting_odds', forecast=forbidden)),
        ):
            with self.subTest(name=name):
                results, coins, _, summary = self.run_case(
                    {'legacy': agent(.6)}, 1, student2_adapters={name: adapter},
                    student2_inputs={'2026-11-07': snapshot}, settlement_contract=settlement,
                )
                self.assertTrue(pd.isna(results.iloc[0]['p_' + name]))
                self.assertEqual(results.iloc[0]['average'], .6)
                self.assertEqual(coins.iloc[0]['coins_' + name], 100)
                self.assertEqual(coins.iloc[0]['weight_' + name], 0)
                self.assertIn('no compatible verified live evidence',
                              json.loads(results.iloc[0]['abstentions'])[name])
                self.assertEqual(summary.set_index('method').loc['average', 'evaluated_days'], 1)

    def test_invalid_csv_closes_rejected(self):
        for prices in ([100, float('nan')], [100, -1]):
            with self.assertRaises(ValueError):
                self.run_case({'a': agent(.5)}, closes=prices)
        with self.assertRaises(ValueError):
            self.run_case({'a': agent(.5)}, dates=['2026-11-07', '2026-11-07'])


if __name__ == '__main__':
    unittest.main()

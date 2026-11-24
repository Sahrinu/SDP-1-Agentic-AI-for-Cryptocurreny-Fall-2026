"""SYNTHETIC offline runner demonstration. No real forecasts or model/API calls."""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('demo_runner', ROOT / 'experiments/run.py')
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class SyntheticAdapter:
    def __init__(self, kind):
        self.kind = kind

    def forecast(self, day, data):
        missing = (self.kind == 'betting_odds' or day == '2026-11-08'
                   or (self.kind == 'news' and data.get('fake_headline')))
        return {'p_up': None if missing else {'news': .7, 'mood': .6}[self.kind],
                'data_missing': missing,
                'limitations': ['SYNTHETIC demonstration; not a real specialist forecast']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(ROOT / 'experiments/results/synthetic_abstention_demo'))
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        parser.error('Choose an empty output directory to avoid overwriting prior results')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'data').mkdir()
        (root / 'agents').mkdir()
        pd.DataFrame({'date': ['2026-11-07', '2026-11-08', '2026-11-09'],
                      'close': [100, 110, 105]}).to_csv(root / 'data/btc_prices.csv', index=False)
        common = {'price_source': 'SYNTHETIC_TEST_FEED',
                  'reference_price_definition': 'synthetic_close', 'tie_definition': 'down'}
        close_times = {f'2026-11-{d:02d}': f'2026-11-{d:02d}T17:00:00Z' for d in (7, 8, 9)}
        days = {f'2026-11-{d:02d}': {'student2_forecast': dict(common,
                prediction_time=close_times[f'2026-11-{d:02d}'],
                target_time=close_times[f'2026-11-{d+1:02d}'])} for d in (7, 8)}
        print('SYNTHETIC DEMO: all agent responses and prices are fabricated test inputs.')
        runner.run('2026-11-07', '2026-11-08', 2, root / 'data', root / 'agents', out,
                   specialist_adapters={'News Specialist': SyntheticAdapter('news'),
                                      'Mood Specialist': SyntheticAdapter('mood'),
                                      'Betting Odds Specialist': SyntheticAdapter('betting_odds')},
                   specialist_inputs=days, settlement_contract=dict(common, close_times=close_times))
        (out / 'DEMO_METADATA.json').write_text(json.dumps({
            'mode': 'synthetic_offline', 'real_specialists_executed': False,
            'model_or_network_calls': False,
            'purpose': 'Exercise the runner registration boundary, abstentions, scores and attack dropout',
            'days': days, 'settlement': dict(common, close_times=close_times)}, indent=2))


if __name__ == '__main__':
    main()

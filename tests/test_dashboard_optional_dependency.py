"""Optional betting collection must not discard other dashboard sources."""
import builtins
import json

import pandas as pd


def test_missing_betting_dependency_keeps_other_collected_inputs(tmp_path, monkeypatch):
    from market_data import live, yahoo_dashboard

    def unavailable(*args, **kwargs):
        raise RuntimeError('Offline test source unavailable')

    prices = pd.DataFrame(
        {'open': [100.0], 'close': [100.0]},
        index=pd.DatetimeIndex(['2026-11-15'], tz='UTC', name='date'))
    monkeypatch.setattr(live, 'collect_bitcoin', lambda: prices)
    monkeypatch.setattr(live, 'collect_chain', unavailable)
    monkeypatch.setattr(live, 'get_json', unavailable)
    monkeypatch.setattr(yahoo_dashboard, 'collect_yahoo', unavailable)
    monkeypatch.delenv('COINGECKO_DEMO_API_KEY', raising=False)

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {'market_data.prediction_markets.dashboard_collect',
                    'market_data.prediction_markets.limitless_live'}:
            raise ModuleNotFoundError('optional dependency not installed')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    data, statuses = live.collect_snapshot(tmp_path)

    assert data['btc_prices'].equals(prices)
    assert statuses['btc_prices']['status'] == 'collected'
    assert statuses['betting_odds'] == {
        'status': 'unavailable',
        'reason': 'A fresh public Kalshi BTC trade is requested immediately before Generate 24h forecast',
    }
    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['sources']['betting_odds'] == statuses['betting_odds']
    assert (tmp_path / 'btc_prices.csv').is_file()

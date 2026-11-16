"""Serve the existing dashboard with controlled synthetic offline fixtures.

All action endpoints are disabled. Paper fixtures come from the real paper risk
engine in a new isolated output directory, never from an exchange.
"""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sys
import uuid

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.activity import ForecastActivity
from dashboard.service import forecast_snapshot, json_safe
from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy

NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
SCENARIOS = ('mixed', 'abstain', 'executed', 'blocked', 'halted', 'hold', 'unconfigured')


def forecast_fixture(all_abstain=False):
    dates = pd.date_range(end='2026-11-14', periods=1100, tz='UTC')
    prices = pd.DataFrame({'close': [10000 * math.exp(i * .001 + .04 * math.sin(i / 10))
                                    for i in range(1100)]}, index=dates)
    quote = {'price': 50000, 'bar_close_time': (NOW-timedelta(seconds=1)).isoformat(),
             'retrieved_at': NOW.isoformat(), 'source': 'synthetic offline fixture'}
    activity = ForecastActivity()
    activity.begin()
    forecast = forecast_snapshot({} if all_abstain else {'btc_prices': prices}, quote,
                                 decision_time=NOW, on_progress=activity.record)
    activity.finish()
    forecast['mode'] = 'synthetic offline fixture'
    forecast['limitations'].insert(0, 'Synthetic offline fixture; no provider was contacted.')
    return forecast, activity.snapshot()


def paper_fixtures(directory):
    trader = PaperTrader('10000')
    policy = RiskPolicy('.30', '.10')  # synthetic values, not agreed policy
    path = directory / 'risk.sqlite3'
    RiskGateway.initialize_state(path, policy=policy, trader=trader,
        actor='offline-demo', reason='Synthetic fixture initialization',
        timestamp=NOW-timedelta(minutes=1), previous_forecast='.50')
    gateway = RiskGateway(trader, policy, state_path=path)

    def record(action, probability, minute, fraction='.10'):
        result = gateway.execute(action, probability_up=probability, btc_price_usd='50000',
                                 timestamp=NOW+timedelta(minutes=minute), buy_fraction=fraction)
        return {'requested': action, 'risk': {'action': action, 'status': result['status'],
                    'reason': result['reason']},
                'breaker': {'configured': True, 'halted': result['breaker_halted'],
                            'reason': result['reason']},
                'trade': result['trade'], 'portfolio': trader.summary('50000'),
                'synthetic': True, 'risk_outcome': result}

    executed = record('BUY', '.60', 0)
    blocked = record('BUY', '.61', 1, '.90')
    hold = record('HOLD', '.62', 2)
    halted = record('SELL', '.10', 3)
    gateway.close()
    unconfigured = {'requested': 'BUY', 'risk': {'status': 'blocked',
        'reason': 'Explicit risk policy, initialized durable state and buy fraction are required'},
        'breaker': {'configured': False, 'halted': False, 'reason': None},
        'trade': None, 'portfolio': PaperTrader('10000').summary('50000'), 'synthetic': True}
    return {'executed': executed, 'blocked': blocked, 'halted': halted, 'hold': hold,
            'unconfigured': unconfigured}


def make_server(scenario='mixed', port=8765):
    directory = ROOT / 'outputs' / 'offline_dashboard' / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    forecast, activity = forecast_fixture(scenario == 'abstain')
    papers = paper_fixtures(directory)
    state = {'busy': False, 'message': 'SYNTHETIC OFFLINE FIXTURE: ' + scenario,
             'sources': {}, 'forecast': forecast, 'paper': papers.get(scenario), 'replay': None,
             'activity': activity, 'gemini_configured': False,
             'gemini': {'configured': False, 'source': None}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, data, kind):
            raw = data.encode('utf-8') if isinstance(data, str) else json.dumps(json_safe(data), allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',
                                                f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, {'error': 'Invalid host'}, 'application/json')
            if self.path == '/':
                html = (ROOT/'dashboard/index.html').read_text(encoding='utf-8').replace('__TOKEN__', 'offline-disabled')
                banner = ('<div style="position:sticky;top:0;z-index:10000;padding:10px;background:#ffe18a;'
                          'color:#111;text-align:center;font-weight:bold">SYNTHETIC OFFLINE FIXTURE: '
                          + scenario + ' &mdash; forecast and paper cases are separate examples; all actions disabled; no provider calls</div>')
                return self.respond(200, html.replace('<body>', '<body>'+banner, 1), 'text/html')
            if self.path in ('/app.js', '/styles.css'):
                kind = 'text/javascript' if self.path.endswith('.js') else 'text/css'
                return self.respond(200, (ROOT/'dashboard'/self.path[1:]).read_text(encoding='utf-8'), kind)
            if self.path == '/api/state':
                return self.respond(200, state, 'application/json')
            if self.path == '/api/market':
                return self.respond(200, {'candles': [], 'status': 'synthetic fixture has no chart'}, 'application/json')
            return self.respond(404, {'error': 'Not found'}, 'application/json')

        def do_POST(self):
            return self.respond(403, {'error': 'Offline fixture mode: all actions are disabled'}, 'application/json')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler), directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='mixed')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server, directory = make_server(args.scenario, args.port)
    print(f'SYNTHETIC OFFLINE dashboard: http://127.0.0.1:{server.server_port} ({args.scenario})', flush=True)
    print(f'Isolated synthetic risk state: {directory}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

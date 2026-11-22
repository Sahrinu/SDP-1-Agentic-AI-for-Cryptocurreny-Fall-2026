"""Run with python -m dashboard; loopback-only HTTP service."""
import argparse
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from dashboard.credentials import SessionCredentials, load_dashboard_environment, parse_key_payload
from dashboard.activity import ForecastActivity
from dashboard.valuation import PortfolioValuation

from dashboard.service import forecast_snapshot,PaperStore,json_safe,market_snapshot,recent_news_snapshot
from exchange.ledger import LiveLedger

ROOT = Path(__file__).resolve().parents[1]


def create_server(port=8765, *, root=ROOT, bind=True):
    root = Path(root)
    load_dashboard_environment(root)
    credentials = SessionCredentials()
    activity = ForecastActivity()
    token = secrets.token_urlsafe(32)
    state = {'busy':False,'message':'Collect live inputs to begin. No forecast has run.',
             'sources':{},'forecast':None,'paper':None,'replay':None}
    data = {}
    lock = threading.Lock()
    store = PaperStore(root/'outputs/dashboard/paper.sqlite3')
    valuation = PortfolioValuation(store)
    state['paper'] = store.latest_decision()
    ledger = LiveLedger(root/'outputs/dashboard/live_ledger.sqlite3')
    latest_snapshot = {'directory':None}  # where the last collect saved its inputs
    state['learning'] = ledger.summary()
    def settle_live():
        # Pay out every live forecast whose 24h window has ended, oldest first.
        try:
            from market_data.live import target_quote
            outcome = ledger.settle_due(target_quote)
            problem = outcome['problem']
        except Exception as exc:
            outcome, problem = {'settled':[]}, {'reason':type(exc).__name__}
        summary = ledger.summary()
        summary['just_settled'] = len(outcome['settled'])
        summary['problem'] = problem
        with lock:
            state['learning'] = summary
    def collect_inputs():
        from market_data.live import collect_snapshot
        directory = root/'outputs/dashboard/snapshots'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        collected, sources = collect_snapshot(directory, input_directory=root/'data')
        with lock:
            data.clear()
            data.update(collected)
            state['sources'] = sources
            latest_snapshot['directory'] = str(directory)

    def work(action, requested_action=None):
        try:
            if action == 'collect':
                collect_inputs()
                settle_live()
                with lock:
                    state.update(forecast=None,paper=None,debate=None,message='Collection finished. Inspect source status, then run forecast.')
            elif action == 'forecast':
                from market_data.live import collect_news, collect_recent_bitcoin, reference_quote, refresh_betting_inputs
                settle_live()
                prices = data.get('btc_prices')
                prior_day = datetime.now(timezone.utc).date() - timedelta(days=1)
                if prices is not None and not prices.empty and prices.index.max().date() < prior_day:
                    with lock:
                        state['message'] = 'Refreshing live inputs before forecasting.'
                    collect_inputs()
                with lock:
                    forecast_data = dict(data)
                news_capture = None
                if recent_news_snapshot(forecast_data, at=datetime.now(timezone.utc)):
                    news_status = {'status':'collected','reused':True,
                        'received_at':forecast_data['student2_news_observed_at']}
                    # Record where the reused articles are archived, for later attack tests.
                    news_capture = latest_snapshot['directory']
                else:
                    forecast_data.pop('student2_news_articles', None)
                    forecast_data.pop('student2_news_observed_at', None)
                    try:
                        articles = collect_news()
                        if not isinstance(articles, list) or not articles:
                            raise ValueError('No BTC news articles returned')
                        observed_at = datetime.now(timezone.utc).isoformat()
                        capture_dir = root/'outputs/dashboard/news'
                        capture_dir.mkdir(parents=True, exist_ok=True)
                        destination = capture_dir/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+secrets.token_hex(4)+'.json')
                        destination.write_text(json.dumps({'observed_at':observed_at,'articles':articles}, default=str, indent=2))
                        news_capture = str(destination)
                        forecast_data['student2_news_articles'] = articles
                        forecast_data['student2_news_observed_at'] = observed_at
                        with lock:
                            data['student2_news_articles'] = articles
                            data['student2_news_observed_at'] = observed_at
                        news_status = {'status':'collected','received_at':observed_at}
                    except Exception as exc:
                        news_status = {'status':'unavailable','reason':type(exc).__name__}
                        with lock:
                            data.pop('student2_news_articles', None)
                            data.pop('student2_news_observed_at', None)
                with lock:
                    state['sources'] = dict(state['sources'], news=news_status)
                intraday_capture = None
                if forecast_data.get('btc_prices') is not None:
                    try:
                        recent = collect_recent_bitcoin()
                        capture_dir = (root/'outputs/dashboard/intraday'/
                            (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+secrets.token_hex(4)))
                        capture_dir.mkdir(parents=True,exist_ok=False)
                        for name, frame in recent.items():
                            frame.to_csv(capture_dir/(name+'.csv'),index_label='close_time')
                        forecast_data.update(recent)
                        intraday_capture = str(capture_dir)
                        intraday_status = {'status':'collected',
                            'received_at':datetime.now(timezone.utc).isoformat()}
                    except Exception as exc:
                        intraday_status = {'status':'unavailable','reason':type(exc).__name__}
                    with lock:
                        state['sources'] = dict(state['sources'], btc_intraday=intraday_status)
                client = None
                if credentials.public_status()["configured"]:
                    from model_clients.demo_gemini import DemoGemini
                    client = DemoGemini(api_key=credentials.resolved_key())
                with lock:
                    state['message'] = 'Checking recent public Kalshi BTC trades before forecasting.'
                book_directory = root/'outputs/dashboard/books'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                book_status = refresh_betting_inputs(forecast_data, book_directory)
                with lock:
                    state['sources']['betting_odds'] = book_status
                try:
                    result = forecast_snapshot(forecast_data,reference_quote(),client=client,on_progress=activity.record,ledger=ledger)
                finally:
                    if client is not None:
                        client.close()
                result['intraday_capture'] = intraday_capture
                result['news_capture'] = news_capture
                activity.finish()
                result["activity"] = activity.snapshot()
                directory = root/'outputs/dashboard/forecasts'
                directory.mkdir(parents=True,exist_ok=True)
                destination = directory/(result['id']+'.json')
                temporary = destination.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(result,default=str,indent=2))
                temporary.replace(destination)
                try:
                    ledger.record(result)
                    learning = ledger.summary()
                except Exception as exc:
                    learning = dict(ledger.summary(), problem={'reason':'Forecast not recorded for settlement: '+type(exc).__name__})
                with lock:
                    state.update(forecast=result,paper=None,debate=None,learning=learning,message='Fresh forecast saved. Outcome is pending for 24 hours; it is settled automatically on the next collect or forecast after that.')
            elif action == 'debate':
                from experiments.debate import debate
                from model_clients.demo_gemini import DemoGemini
                client = DemoGemini(api_key=credentials.resolved_key())
                try:
                    result = debate(state['forecast'], client)
                finally:
                    client.close()
                result['usage'] = client.usage
                with lock:
                    state.update(debate=result,message='Debate baseline completed on the same saved evidence. It does not alter the Exchange vote.')
            elif action == 'paper':
                result = store.decide(state['forecast'], requested_action=requested_action)
                valuation.record(state['forecast']['quote'])
                with lock:
                    state.update(paper=result,message='Paper decision recorded. Repeating this decision cannot place a duplicate trade.')
            elif action == 'archive-replay':
                from dashboard.archive import replay_saved_forecasts
                from market_data.live import target_quote
                directory = root/'outputs/dashboard'
                destination = directory/'archive_replays'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                replay = replay_saved_forecasts(directory/'forecasts', destination, target_quote)
                with lock:
                    state.update(replay=replay, message=f"Settled {len(replay['coins'])} archived live forecasts for review.")
            elif action == 'real-replay':
                from agents.adapters import Student2Adapter
                from agents.registry import CATALOG
                from experiments.run import run
                from experiments.paper import TradingPolicy
                from experiments.evaluation import trading_baselines
                import pandas as pd
                frame = data.get('btc_prices')
                if frame is None or frame.empty:
                    raise ValueError('Collect real BTC history first')
                directory = root/'outputs/dashboard/real_replays'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                inputs = directory/'inputs';inputs.mkdir(parents=True)
                frame.to_csv(inputs/'btc_prices.csv',index_label='date')
                end = frame.index[-1].date()-pd.Timedelta(days=1)
                start = end-pd.Timedelta(days=59)
                adapters = [Student2Adapter(spec.registration.split(':')[1])
                            for spec in CATALOG if spec.registration.startswith('adapter:')]
                results,coins,attacks,summary = run(str(start),str(end),0,inputs,ROOT/'agents',directory,
                    student2_adapters={adapter.AGENT_NAME: adapter for adapter in adapters},
                    paper_policy=TradingPolicy(circuit_breaker=True),synthetic=False)
                baselines = trading_baselines(frame,start=str(start),end=str(end),output=directory/'baselines')
                with lock:
                    state.update(replay={'mode':'REAL BTC historical replay: price-based agents only; absent non-price vintages abstain',
                        'summary':summary.to_dict(orient='records'),'results':results.to_dict(orient='records'),
                        'coins':coins.to_dict(orient='records'),'baselines':baselines,
                        'output':str(directory)},message='Real price-only replay complete. This is not the full 13-agent experiment.')
            elif action == 'replay':
                from experiments.run import run
                from experiments.paper import TradingPolicy
                directory = root/'outputs/dashboard/replays'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                results,coins,attacks,summary = run('2026-10-01','2026-10-31',5,
                    root/'tests/fake_data',root/'tests/fake_agents',directory,
                    paper_policy=TradingPolicy(execution='next_close'),synthetic=True)
                with lock:
                    state.update(replay={'mode':'SYNTHETIC workflow replay; fake agents and prices',
                        'summary':summary.to_dict(orient='records'),'results':results.to_dict(orient='records'),
                        'coins':coins.to_dict(orient='records'),
                        'output':str(directory)},message='Synthetic workflow replay complete. These are not research performance results.')
        except Exception as exc:
            if action == 'forecast':
                activity.fail(type(exc).__name__)
            with lock:
                state['message'] = 'Action failed: '+type(exc).__name__+'. Check source access, configuration and data requirements.'
        finally:
            with lock:
                state['busy'] = False
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def respond(self,status,payload,kind='application/json'):
            raw = payload.encode() if isinstance(payload,str) else json.dumps(json_safe(payload),default=str,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind+'; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(raw)
        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        def do_GET(self):
            if not self.valid_host():
                return self.respond(403,{'error':'Invalid host'})
            if self.path == '/':
                return self.respond(200,(Path(__file__).with_name('index.html')).read_text(encoding='utf-8').replace('__TOKEN__',token),'text/html')
            assets = {'/app.js':('app.js','text/javascript'), '/styles.css':('styles.css','text/css')}
            if self.path in assets:
                name,kind = assets[self.path]
                return self.respond(200,Path(__file__).with_name(name).read_text(encoding='utf-8'),kind)
            if self.path == '/api/market':
                with lock:
                    payload = market_snapshot(data,state['sources'])
                return self.respond(200,payload)
            if self.path == '/api/portfolio':
                try:
                    return self.respond(200,valuation.snapshot())
                except (ValueError,KeyError,sqlite3.Error,OSError):
                    return self.respond(503,{'error':'Portfolio valuation is unavailable; account history is unchanged'})
            if self.path == '/api/state':
                with lock:
                    public = credentials.public_status()
                    payload = dict(state,gemini_configured=public["configured"],gemini=public,activity=activity.snapshot(),paper_settings=store.settings())
                return self.respond(200,payload)
            self.respond(404,{'error':'Not found'})
        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.valid_host() or self.headers.get('X-Demo-Token') != token or origin not in {None,f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
                return self.respond(403,{'error':'Invalid local request'})
            requested_action = None
            if self.path in {'/api/paper/settings','/api/paper'}:
                try:
                    length = int(self.headers.get('Content-Length','0'))
                    if not 0 <= length <= 1024 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('Invalid paper request size')
                    self.connection.settimeout(5)
                    payload = json.loads(self.rfile.read(length)) if length else {}
                    if not isinstance(payload,dict):
                        raise ValueError('Paper settings must be an object')
                    if self.path == '/api/paper/settings':
                        with lock:
                            if state['busy']:
                                return self.respond(409,{'error':'Wait for the current action to finish'})
                            if state['forecast'] is None:
                                raise ValueError('Generate a fresh live forecast before enabling paper trading')
                            settings = store.configure(state['forecast'], **payload)
                            state['message'] = 'Paper trading enabled. Choose Follow forecast or a manual simulated action, then record one decision per fresh forecast.'
                        return self.respond(200,{'paper_settings':settings})
                    if set(payload) - {'action'}:
                        raise ValueError('Unknown paper request fields')
                    requested_action = payload.get('action')
                    if requested_action is not None and requested_action not in ('BUY','SELL','HOLD'):
                        raise ValueError('Paper action must be BUY, SELL or HOLD')
                except (ValueError,TypeError,KeyError,UnicodeError,OSError,sqlite3.Error) as exc:
                    message = str(exc) if type(exc) is ValueError else 'Invalid paper settings or unavailable risk state'
                    return self.respond(400,{'error':message})
            if self.path in {'/api/gemini','/api/gemini/clear'}:
                with lock:
                    if state['busy']:
                        return self.respond(409,{'error':'Wait for the current action before changing the session key'})
                    if self.path.endswith('/clear'):
                        credentials.clear()
                    else:
                        try:
                            length = int(self.headers.get('Content-Length','0'))
                            if not 1 <= length <= 8192 or self.headers.get('Transfer-Encoding'):
                                return self.respond(400,{'error':'Invalid key request size'})
                            self.connection.settimeout(5)
                            key = parse_key_payload(self.rfile.read(length))
                        except (ValueError,UnicodeError,OSError):
                            return self.respond(400,{'error':'Enter a valid Gemini key; it will not be echoed or saved'})
                        credentials.set_key(key)
                    return self.respond(200,{'gemini':credentials.public_status()})
            action = self.path.removeprefix('/api/')
            if action not in {'collect','forecast','paper','replay','debate','real-replay','archive-replay'} or self.path != '/api/'+action:
                return self.respond(404,{'error':'Unknown action'})
            with lock:
                if state['busy']:
                    return self.respond(409,{'error':'An action is already running'})
                if action in {'paper','debate'} and state['forecast'] is None:
                    return self.respond(400,{'error':'Run a fresh forecast first'})
                if action == 'forecast':
                    activity.begin()
                    state.update(forecast=None,debate=None)
                elif action == 'collect':
                    activity.reset()
                state.update(busy=True,message=action.capitalize()+' running…')
            threading.Thread(target=work,args=(action,requested_action),daemon=True).start()
            self.respond(202,{'accepted':True})
    class DashboardServer(ThreadingHTTPServer):
        def serve_forever(self, poll_interval=.5):
            valuation.start()
            try:
                return super().serve_forever(poll_interval)
            finally:
                valuation.stop()

        def server_close(self):
            valuation.stop()
            super().server_close()
    return DashboardServer(('127.0.0.1',port),Handler,bind_and_activate=bind)


def serve(port=8765):
    server = create_server(port)
    print(f'Dashboard: http://127.0.0.1:{server.server_port} (paper trading only)',flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    serve(parser.parse_args().port)

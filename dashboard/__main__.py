"""Run with python -m dashboard; loopback-only HTTP service."""
import argparse
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from dashboard.credentials import SessionCredentials, parse_key_payload
from dashboard.activity import ForecastActivity

from dashboard.service import forecast_snapshot,PaperStore,json_safe,market_snapshot

ROOT = Path(__file__).resolve().parents[1]


def create_server(port=8765, *, root=ROOT, bind=True):
    root = Path(root)
    credentials = SessionCredentials()
    activity = ForecastActivity()
    token = secrets.token_urlsafe(32)
    state = {'busy':False,'message':'Collect live inputs to begin. No forecast has run.',
             'sources':{},'forecast':None,'paper':None,'replay':None}
    data = {}
    lock = threading.Lock()
    store = PaperStore(root/'outputs/dashboard/paper.sqlite3')
    state['paper'] = store.latest_decision()
    def work(action):
        try:
            if action == 'collect':
                from market_data.live import collect_snapshot
                directory = root/'outputs/dashboard/snapshots'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                collected,sources = collect_snapshot(directory, input_directory=root/"data")
                with lock:
                    data.clear(); data.update(collected)
                    state.update(sources=sources,forecast=None,paper=None,debate=None,message='Collection finished. Inspect source status, then run forecast.')
            elif action == 'forecast':
                from market_data.live import reference_quote
                client = None
                if credentials.public_status()["configured"]:
                    from model_clients.demo_gemini import DemoGemini
                    client = DemoGemini(api_key=credentials.resolved_key())
                result = forecast_snapshot(data,reference_quote(),client=client,on_progress=activity.record)
                activity.finish()
                result["activity"] = activity.snapshot()
                directory = root/'outputs/dashboard/forecasts'
                directory.mkdir(parents=True,exist_ok=True)
                destination = directory/(result['id']+'.json')
                temporary = destination.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(result,default=str,indent=2))
                temporary.replace(destination)
                with lock:
                    state.update(forecast=result,paper=None,debate=None,message='Fresh forecast saved. Outcome is pending for 24 hours.')
            elif action == 'debate':
                from experiments.debate import debate
                from model_clients.demo_gemini import DemoGemini
                client = DemoGemini(api_key=credentials.resolved_key())
                result = debate(state['forecast'], client)
                result['usage'] = client.usage
                with lock:
                    state.update(debate=result,message='Debate baseline completed on the same saved evidence. It does not alter the Exchange vote.')
            elif action == 'paper':
                result = store.decide(state['forecast'])
                with lock:
                    state.update(paper=result,message='Paper decision recorded. Repeating this decision cannot place a duplicate trade.')
            elif action == 'real-replay':
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
                results,coins,attacks,summary = run(str(start),str(end),0,inputs,ROOT/'agents',directory,
                    paper_policy=TradingPolicy(circuit_breaker=True),synthetic=False)
                baselines = trading_baselines(frame,start=str(start),end=str(end),output=directory/'baselines')
                with lock:
                    state.update(replay={'mode':'REAL BTC historical replay: price-based agents only; absent non-price vintages abstain',
                        'summary':summary.to_dict(orient='records'),'results':results.to_dict(orient='records'),
                        'baselines':baselines,'output':str(directory)},message='Real price-only replay complete. This is not the full 13-agent experiment.')
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
                return self.respond(200,(Path(__file__).with_name('index.html')).read_text().replace('__TOKEN__',token),'text/html')
            assets = {'/app.js':('app.js','text/javascript'), '/styles.css':('styles.css','text/css')}
            if self.path in assets:
                name,kind = assets[self.path]
                return self.respond(200,Path(__file__).with_name(name).read_text(),kind)
            if self.path == '/api/market':
                with lock:
                    payload = market_snapshot(data,state['sources'])
                return self.respond(200,payload)
            if self.path == '/api/state':
                with lock:
                    public = credentials.public_status()
                    payload = dict(state,gemini_configured=public["configured"],gemini=public,activity=activity.snapshot())
                return self.respond(200,payload)
            self.respond(404,{'error':'Not found'})
        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.valid_host() or self.headers.get('X-Demo-Token') != token or origin not in {None,f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
                return self.respond(403,{'error':'Invalid local request'})
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
            if action not in {'collect','forecast','paper','replay','debate','real-replay'} or self.path != '/api/'+action:
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
            threading.Thread(target=work,args=(action,),daemon=True).start()
            self.respond(202,{'accepted':True})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler,bind_and_activate=bind)


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

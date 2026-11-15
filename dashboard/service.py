"""Demo orchestration shared by the HTTP UI and tests."""
from datetime import datetime, timedelta, timezone
import importlib
import json
import math
import os
from pathlib import Path
from dataclasses import asdict
from decimal import Decimal
import sqlite3
import uuid

from agents.registry import CATALOG
from exchange.engine import Exchange
from exchange.average import average
from exchange.vote import vote
from risk.auditor import audit_forecast
from risk.circuit_breaker import CircuitBreaker, constrained_action
from risk.gateway import execute_guarded
from execution.paper_trading import PaperTrader, Trade


def json_safe(value):
    """Represent missing replay metrics as JSON null, never nonstandard NaN."""
    if isinstance(value, dict):
        return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def market_snapshot(data, sources):
    """Bounded chart payload from collected bars; never invent market statistics."""
    frame = data.get('btc_prices')
    if frame is None or frame.empty:
        return {'candles':[], 'source':'Binance BTCUSDT', 'received_at':None,
                'interval':'1d', 'status':'not_collected'}
    candles=[]
    for stamp,row in frame.tail(1100).iterrows():
        bar={'date':str(stamp.date()),'open':float(row['open']),'close':float(row['close'])}
        for field in ('high','low','volume'):
            value=row.get(field)
            if value is not None and math.isfinite(float(value)):
                bar[field]=float(value)
        candles.append(bar)
    return {'candles':candles,'source':'Binance BTCUSDT','interval':'1d',
            'received_at':sources.get('btc_prices',{}).get('received_at'),
            'status':'collected','price_units':'USDT',
            'basis':'Completed UTC daily candles; latest minute quote is shown separately after forecasting'}


def configured():
    return bool(os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY'))


def forecast_snapshot(data, quote, *, client=None, decision_time=None, on_progress=None):
    instant = decision_time or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('Aware decision time required')
    instant = instant.astimezone(timezone.utc)
    received = datetime.fromisoformat(quote['retrieved_at'])
    bar_time = datetime.fromisoformat(quote['bar_close_time'])
    if not received.tzinfo or not bar_time.tzinfo or not bar_time <= received <= instant or (instant-bar_time).total_seconds() > 180:
        raise ValueError('Reference quote is stale, future-dated or unverified')
    if isinstance(quote['price'], bool) or not math.isfinite(float(quote['price'])) or float(quote['price']) <= 0:
        raise ValueError('Invalid reference price')
    day = instant.date().isoformat()
    view = dict(data, _decision_time=instant.isoformat())
    contract = {'prediction_time':instant.isoformat(),'target_time':(instant+timedelta(hours=24)).isoformat(),
        'price_source':'binance:BTC/USDT',
        'reference_price_definition':'last completed UTC one-minute close at or before each contract timestamp',
        'tie_definition':'down'}
    view['student2_forecast'] = contract
    results, active = [], {}
    for spec in CATALOG:
        if on_progress is not None:
            on_progress({'event':'agent_started','role':spec.name})
        failed = False
        if spec.name == 'Contrarian':
            view['peer_forecasts'] = {'day':day,'probabilities':dict(active)}
        try:
            if spec.registration.startswith('adapter:'):
                from agents.adapters import SpecialistAdapter
                module = SpecialistAdapter(spec.registration.split(':')[1], model_client=client)
            else:
                module = importlib.import_module(spec.module)
            result = module.forecast(day,view)
            audit = audit_forecast(result,day=day,prediction_time=instant)
        except Exception as exc:
            failed = True
            result = {'agent':spec.name,'day':day,'p_up':None,'data_missing':True,
                'evidence':['Component unavailable: '+type(exc).__name__]}
            audit = {'passed':False,'reasons':result['evidence']}
        result['role'] = spec.name
        result['audit'] = audit
        if audit['passed']:
            active[result['agent']] = float(result['p_up'])
        else:
            result['p_up'] = None
        results.append(result)
        if on_progress is not None:
            on_progress({'event':'agent_completed','role':spec.name,'result':result,
                'status':'failed' if failed else 'forecasted' if audit['passed'] else 'abstained'})
    if on_progress is not None:
        on_progress({'event':'aggregation_started'})
    exchange = Exchange(list(active))
    combined = {'exchange':exchange.team_forecast(active),'vote':vote(active),'average':average(active)} if active else dict.fromkeys(('exchange','vote','average'))
    if on_progress is not None:
        on_progress({'event':'aggregation_completed','methods':combined})
    return {'id':uuid.uuid4().hex,'mode':'live','created_at':instant.isoformat(),
        'contract':contract,'quote':quote,'agents':results,'active_count':len(active),
        'weights':exchange.weights() if active else {},'cap_feasible':len(active)*.20 >= 1,
        'methods':combined,'coins_basis':'Equal initial coins for this independent live forecast; no unearned historical learning',
        'usage':getattr(client,'usage',[]),
        'limitations':['Daily-feature rules are experimental and uncalibrated for an intraday rolling 24-hour target.',
            'The outcome is pending until target_time; daily/on-chain/macro publication delays remain visible.']}


class PaperStore:
    """Atomic, idempotent paper account. No network or real exchange orders."""
    def __init__(self,path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def latest_decision(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 1').fetchone()
            return json.loads(row[0]) if row else None

    def decide(self,forecast, *, now=None):
        instant = now or datetime.now(timezone.utc)
        created = datetime.fromisoformat(forecast['created_at'])
        if not 0 <= (instant-created).total_seconds() <= 180 or forecast['mode'] != 'live':
            raise ValueError('Paper action requires a live forecast less than three minutes old')
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload FROM decisions WHERE id=?',(forecast['id'],)).fetchone()
            if old:
                return json.loads(old[0])
            row = db.execute('SELECT payload FROM state WHERE id=1').fetchone()
            saved = json.loads(row[0]) if row else {}
            trader = PaperTrader()
            for key in ('cash','btc','cost_basis','realized_pnl','total_fees'):
                if key in saved:
                    setattr(trader,key,Decimal(saved[key]))
            trader.trades = [Trade(**t) for t in saved.get('trades',[])]
            breaker = CircuitBreaker(**saved.get('breaker',{}))
            p = forecast['methods']['exchange']
            price = forecast['quote']['price']
            summary = trader.summary(price)
            status = breaker.check(summary['equity_usd'],p)
            requested = 'HOLD' if p is None else 'BUY' if p >= .55 else 'SELL' if p <= .45 else 'HOLD'
            action = constrained_action(requested,halted=status['halted'],audit_passed=p is not None,cap_feasible=forecast['cap_feasible'])
            risk,trade = execute_guarded(trader,action,price,timestamp=instant)
            result = {'requested':requested,'constrained':action,'risk':risk.to_dict(),
                'breaker':status,'trade':asdict(trade) if trade else None,
                'portfolio':trader.summary(price),'forecast_id':forecast['id'],
                'recorded_at':instant.isoformat(),'reference_quote':forecast['quote'],
                'quote_basis':'Same recently observed reference close, with simulated adverse slippage; not a guaranteed market fill'}
            state = {k:str(getattr(trader,k)) for k in ('cash','btc','cost_basis','realized_pnl','total_fees')}
            state.update(trades=[asdict(t) for t in trader.trades],breaker=asdict(breaker))
            db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(state),))
            db.execute('INSERT INTO decisions VALUES(?,?)',(forecast['id'],json.dumps(result)))
            return result

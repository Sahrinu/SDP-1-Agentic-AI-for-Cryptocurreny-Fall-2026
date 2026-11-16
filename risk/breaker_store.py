"""Durable, transaction-locked circuit state and explicit audit records."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path


def account_state(trader):
    """Bind one state file to one exact simulated account and trade ledger."""
    names=('initial_cash','cash','btc','cost_basis','realized_pnl','total_fees','fee_rate','slippage')
    result={name:str(getattr(trader,name)) for name in names}
    result['trades']=[asdict(trade) for trade in trader.trades]
    return json.dumps(result,sort_keys=True,separators=(',',':'))


def _text(value, name):
    if not isinstance(value,str) or not value.strip() or len(value)>512:
        raise ValueError(f'{name} must be a nonempty string of at most 512 characters')
    return value.strip()


class BreakerStore:
    """SQLite serializes competing processes; missing or invalid state fails closed."""
    def __init__(self, path):
        self.path=Path(path).resolve()
        if not self.path.is_file():raise ValueError('Risk state must be explicitly initialized')
        self.db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None,check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL')

    @staticmethod
    def initialize(path, *, policy, trader, actor, reason, timestamp, previous_forecast):
        from risk.durable_gateway import _aware_time, _probability
        actor=_text(actor,'actor');reason=_text(reason,'reason')
        when=_aware_time(timestamp).isoformat()
        previous=None if previous_forecast is None else str(_probability(previous_forecast,'previous forecast'))
        path=Path(path).resolve()
        path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
        db=None
        try:
            db=sqlite3.connect(path,timeout=0,isolation_level=None)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, halted INTEGER NOT NULL, halt_reason TEXT, previous_forecast TEXT, last_time TEXT, last_order_time TEXT, swing_limit TEXT NOT NULL, loss_limit TEXT NOT NULL, account TEXT NOT NULL)')
            db.execute('CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, timestamp TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES (1,1,0,NULL,?,?,NULL,?,?,?)',
                       (previous,when,str(policy.max_forecast_swing),str(policy.max_cumulative_loss),account_state(trader)))
            db.execute('INSERT INTO audit(event,timestamp,actor,reason) VALUES (?,?,?,?)',('initialize',when,actor,reason))
            db.execute('COMMIT')
        except BaseException:
            if db is not None:
                try:db.execute('ROLLBACK')
                except sqlite3.Error:pass
            raise
        finally:
            if db is not None:db.close()

    def begin(self):
        self.db.execute('BEGIN IMMEDIATE')
        row=self.db.execute('SELECT version,halted,halt_reason,previous_forecast,last_time,last_order_time,swing_limit,loss_limit,account FROM state WHERE id=1').fetchone()
        if row is None or len(row)!=9 or row[0]!=1 or type(row[1]) is not int or row[1] not in (0,1):
            raise ValueError('Invalid durable risk state')
        return row

    def save(self, *, halted, halt_reason, previous_forecast, last_time, last_order_time, trader):
        self.db.execute('UPDATE state SET halted=?,halt_reason=?,previous_forecast=?,last_time=?,last_order_time=?,account=? WHERE id=1',
            (int(halted),halt_reason,None if previous_forecast is None else str(previous_forecast),
             None if last_time is None else last_time.isoformat(),
             None if last_order_time is None else last_order_time.isoformat(),account_state(trader)))

    def audit(self, event, timestamp, actor, reason):
        self.db.execute('INSERT INTO audit(event,timestamp,actor,reason) VALUES (?,?,?,?)',
                        (event,timestamp,_text(actor,'actor'),_text(reason,'reason')))

    def commit(self):self.db.execute('COMMIT')
    def rollback(self):
        try:self.db.execute('ROLLBACK')
        except sqlite3.Error:pass
    def events(self):
        return [dict(event=x[0],timestamp=x[1],actor=x[2],reason=x[3]) for x in
                self.db.execute('SELECT event,timestamp,actor,reason FROM audit ORDER BY id')]
    def close(self):self.db.close()

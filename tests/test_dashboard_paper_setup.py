"""Explicit paper opt-in, manual decisions, and persistent risk enforcement."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from dashboard.service import PaperStore

NOW = datetime(2026, 11, 22, 20, tzinfo=timezone.utc)


def forecast(id='first', p=.48, price=60000, at=NOW, cap=True):
    return {'id':id, 'mode':'live', 'created_at':at.isoformat(),
            'methods':{'exchange':p}, 'cap_feasible':cap,
            'quote':{'price':price, 'retrieved_at':at.isoformat(), 'bar_close_time':at.isoformat()}}


def enable(store, **kwargs):
    return store.configure(forecast(), max_forecast_swing='.2', max_cumulative_loss='.1',
                           buy_fraction=kwargs.get('size', '.1'), now=NOW)


def test_manual_buy_sell_and_restart_keep_account_and_policy(tmp_path):
    path=tmp_path/'paper.db';store=PaperStore(path)
    enable(store)
    first=store.decide(forecast(), now=NOW, requested_action='BUY')
    assert first['decision_mode']=='manual' and first['trade']['action']=='BUY'
    assert 0 < Decimal(first['portfolio']['btc'])*60000 / Decimal(first['portfolio']['equity_usd']) <= Decimal('.25')
    restored=PaperStore(path)
    assert restored.settings()==store.settings()
    assert restored.decide(forecast(), now=NOW, requested_action='SELL')==first
    second=restored.decide(forecast(id='second'), now=NOW+timedelta(seconds=1), requested_action='SELL')
    assert second['trade']['action']=='SELL'
    assert Decimal(second['portfolio']['btc'])==0
    assert second['portfolio']['trade_count']==2
    assert Decimal(second['portfolio']['total_fees_usd'])>0


def test_halt_survives_restart_and_cannot_be_reset_by_enabling_again(tmp_path):
    path=tmp_path/'paper.db';store=PaperStore(path);enable(store)
    store.decide(forecast(),now=NOW,requested_action='BUY')
    halted=PaperStore(path).decide(forecast(id='jump',p=.8),now=NOW+timedelta(seconds=1),requested_action='BUY')
    assert halted['breaker']['halted'] and halted['trade'] is None
    restored=PaperStore(path)
    with pytest.raises(ValueError,match='already saved'):
        enable(restored)
    result=restored.decide(forecast(id='sell',p=.48),now=NOW+timedelta(seconds=2),requested_action='SELL')
    assert result['breaker']['halted'] and result['trade'] is None
    with sqlite3.connect(str(path)+'.risk.sqlite3') as db:
        assert db.execute("SELECT event FROM audit ORDER BY id").fetchall()==[('initialize',),('halt',)]


def test_repeated_buys_remain_bounded_by_total_allocation(tmp_path):
    store=PaperStore(tmp_path/'paper.db');enable(store,size='.2')
    first=store.decide(forecast(),now=NOW,requested_action='BUY')
    second=store.decide(forecast(id='second'),now=NOW+timedelta(seconds=1),requested_action='BUY')
    assert first['trade'] and second['risk']['status']=='blocked'
    assert second['portfolio']['btc']==first['portfolio']['btc']


def test_missing_risk_state_is_never_recreated(tmp_path):
    path=tmp_path/'paper.db';store=PaperStore(path);enable(store)
    (tmp_path/'paper.db.risk.sqlite3').unlink()
    restored=PaperStore(path)
    result=restored.decide(forecast(),now=NOW,requested_action='BUY')
    assert result['trade'] is None and result['risk']['status']=='blocked'
    with pytest.raises(ValueError):enable(restored)
    assert not (tmp_path/'paper.db.risk.sqlite3').exists()


@pytest.mark.parametrize('size',['0','.26','NaN',True])
def test_invalid_size_does_not_initialize_state(tmp_path,size):
    store=PaperStore(tmp_path/'paper.db')
    with pytest.raises(ValueError):enable(store,size=size)
    assert not store.settings()['configured']
    assert not (tmp_path/'paper.db.risk.sqlite3').exists()


def test_setup_and_manual_actions_require_fresh_forecast_and_participation(tmp_path):
    store=PaperStore(tmp_path/'paper.db')
    with pytest.raises(ValueError,match='fresh'):
        store.configure(forecast(at=NOW-timedelta(minutes=4)),max_forecast_swing='.2',
                        max_cumulative_loss='.1',buy_fraction='.1',now=NOW)
    enable(store)
    result=store.decide(forecast(cap=False),now=NOW,requested_action='BUY')
    assert result['trade'] is None and result['risk']['status']=='blocked'
    with pytest.raises(ValueError):
        store.decide(forecast(id='stale'),now=NOW+timedelta(minutes=4),requested_action='BUY')

"""Market valuation changes prices only; account and risk state remain intact."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
import threading
import time

import pytest

from dashboard.service import PaperStore
from dashboard.valuation import PortfolioValuation

NOW=datetime(2026,11,23,10,tzinfo=timezone.utc)


def quote(price=60000, at=NOW):
    return {'price':price,'bar_close_time':(at-timedelta(seconds=1)).isoformat(),
            'retrieved_at':at.isoformat(),'source':'synthetic test quote'}


def forecast(id='buy', price=60000, at=NOW):
    return {'id':id,'mode':'live','created_at':at.isoformat(),'quote':quote(price,at),
            'methods':{'exchange':.48},'cap_feasible':True}


def account(tmp_path):
    store=PaperStore(tmp_path/'paper.db')
    store.configure(forecast(),max_forecast_swing='.2',max_cumulative_loss='.1',buy_fraction='.1',now=NOW)
    store.decide(forecast(),now=NOW,requested_action='BUY')
    return store


def state(store):
    with sqlite3.connect(store.path) as db:
        return db.execute('SELECT payload FROM state').fetchall(),db.execute('SELECT * FROM decisions').fetchall()


def test_value_moves_with_btc_without_trading_or_touching_breaker(tmp_path):
    store=account(tmp_path)
    before=state(store)
    with sqlite3.connect(store.risk_state_path) as db:
        risk_before=db.execute('SELECT * FROM state').fetchall(),db.execute('SELECT * FROM audit').fetchall()
    first=store.value(quote(),now=NOW,record=True)
    later=NOW+timedelta(minutes=1)
    second=store.value(quote(66000,later),now=later,record=True)
    a,b=first['portfolio'],second['portfolio']
    btc=Decimal(a['btc'])
    assert Decimal(b['equity_usd'])-Decimal(a['equity_usd'])==btc*6000
    assert a['cash_usd']==b['cash_usd'] and a['btc']==b['btc'] and a['total_fees_usd']==b['total_fees_usd']
    assert len(second['history'])==2
    assert state(store)==before
    with sqlite3.connect(store.risk_state_path) as db:
        assert risk_before==(db.execute('SELECT * FROM state').fetchall(),db.execute('SELECT * FROM audit').fetchall())
    assert Decimal(b['total_pnl_usd'])==Decimal(b['equity_usd'])-10000
    assert Decimal(b['total_pnl_usd'])==Decimal(b['realized_pnl_usd'])+Decimal(b['unrealized_pnl_usd'])


def test_same_minute_trade_updates_history_and_duplicate_reads_do_not(tmp_path):
    store=PaperStore(tmp_path/'paper.db')
    store.value(quote(),now=NOW,record=True)
    store.configure(forecast(),max_forecast_swing='.2',max_cumulative_loss='.1',buy_fraction='.1',now=NOW)
    store.decide(forecast(),now=NOW,requested_action='BUY')
    first=store.value(quote(),now=NOW,record=True)
    again=store.value(quote(),now=NOW+timedelta(seconds=1),record=True)
    assert len(first['history'])==len(again['history'])==2
    assert Decimal(again['history'][-1]['equity_usd'])<10000
    restored=PaperStore(tmp_path/'paper.db').value(now=NOW+timedelta(seconds=2))
    assert restored['portfolio']==again['portfolio'] and restored['history']==again['history']


def test_sale_stops_price_exposure_and_fees_are_not_double_counted(tmp_path):
    store=account(tmp_path)
    later=NOW+timedelta(minutes=1)
    sold=store.decide(forecast('sell',66000,later),now=later,requested_action='SELL')
    value=store.value(quote(66000,later),now=later,record=True)['portfolio']
    later2=later+timedelta(minutes=1)
    next_value=store.value(quote(70000,later2),now=later2,record=True)['portfolio']
    assert value['equity_usd']==next_value['equity_usd']==sold['portfolio']['equity_usd']
    assert value['btc']=='0' and Decimal(value['unrealized_pnl_usd'])==0
    assert Decimal(value['total_pnl_usd'])==Decimal(value['realized_pnl_usd'])
    assert value['total_fees_usd']==sold['portfolio']['total_fees_usd']


def test_failed_quotes_keep_last_value_and_expose_staleness(tmp_path):
    store=account(tmp_path);clock=[NOW]
    monitor=PortfolioValuation(store,get_quote=lambda:quote(),clock=lambda:clock[0])
    monitor.refresh();before=monitor.snapshot()
    def failure():raise TimeoutError('provider text must not leak')
    monitor.get_quote=failure;clock[0]+=timedelta(minutes=1);monitor.refresh()
    failed=monitor.snapshot()
    assert failed['status']=='stale' and failed['error']=='TimeoutError'
    assert failed['history']==before['history'] and failed['portfolio']==before['portfolio']
    assert 'provider text' not in json.dumps(failed)
    clock[0]+=timedelta(minutes=5)
    restarted=PortfolioValuation(PaperStore(store.path),clock=lambda:clock[0])
    assert restarted.snapshot()['status']=='stale'
    assert restarted.snapshot()['history']==before['history']
    monitor.get_quote=lambda:quote(61000,clock[0]);monitor.refresh()
    assert monitor.snapshot()['status']=='live' and len(monitor.snapshot()['history'])==2


@pytest.mark.parametrize('bad',[
    quote(True),quote(-1),quote(float('nan')),quote(60000,NOW+timedelta(seconds=1)),
    quote(60000,NOW-timedelta(minutes=4)),
    {'price':60000,'bar_close_time':'2026-11-23T10:00:00','retrieved_at':NOW.isoformat()},
])
def test_invalid_quotes_are_not_written(tmp_path,bad):
    store=PaperStore(tmp_path/'paper.db')
    with pytest.raises(ValueError):store.value(bad,now=NOW,record=True)
    assert store.value(now=NOW)['history']==[]


def test_older_quote_never_rewinds_latest_price(tmp_path):
    store=account(tmp_path)
    later=NOW+timedelta(minutes=1)
    store.value(quote(66000,later),now=later,record=True)
    current=store.value(quote(60000),now=later,record=True)
    assert current['quote']['price']==66000 and len(current['history'])==1


def test_empty_account_needs_no_price_to_show_cash(tmp_path):
    store=PaperStore(tmp_path/'paper.db')
    monitor=PortfolioValuation(store,clock=lambda:NOW)
    snapshot=monitor.snapshot()
    assert snapshot['status']=='waiting'
    assert snapshot['portfolio']['cash_usd']==snapshot['portfolio']['equity_usd']=='10000'
    assert snapshot['quote'] is None and snapshot['history']==[]


def test_background_refresh_runs_without_a_browser_or_forecast(tmp_path):
    called=threading.Event()
    def get():called.set();return quote()
    monitor=PortfolioValuation(PaperStore(tmp_path/'paper.db'),get_quote=get,interval=.01,clock=lambda:NOW)
    monitor.start()
    assert called.wait(1)
    deadline=time.monotonic()+2
    while monitor.snapshot()['status']!='live' and time.monotonic()<deadline:
        time.sleep(.01)
    monitor.stop()
    assert monitor.snapshot()['status']=='live'
    assert len(monitor.snapshot()['history'])==1

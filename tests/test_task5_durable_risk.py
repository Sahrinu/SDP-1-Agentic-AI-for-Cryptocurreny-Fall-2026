"""Synthetic offline regressions for durable paper-risk decisions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from execution.paper_trading import PaperTrader
from risk.durable_gateway import RiskGateway, RiskPolicy

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
POLICY = RiskPolicy('.2', '.1')  # Synthetic thresholds, not a production policy.


def initialize(path, trader):
    RiskGateway.initialize_state(path, policy=POLICY, trader=trader,
        actor='synthetic-test', reason='Initialize isolated test account',
        timestamp=NOW-timedelta(minutes=1), previous_forecast='.5')


def snapshot(trader):
    return (trader.cash, trader.btc, trader.cost_basis, trader.realized_pnl,
            trader.total_fees, tuple(trader.trades))


def test_halt_survives_restart_and_explicit_reset_is_audited(tmp_path):
    path=tmp_path/'risk.sqlite3'
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    initialize(path,trader)
    first=RiskGateway(trader,POLICY,state_path=path)
    try:
        before=snapshot(trader)
        result=first.execute('BUY',probability_up='.7',btc_price_usd='100',
                             timestamp=NOW,buy_fraction='.1')
        assert result['status']=='halted'
        assert snapshot(trader)==before
        assert [e['event'] for e in first.audit_events()]==['initialize','halt']
    finally:
        first.close()

    restarted=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    second=RiskGateway(restarted,POLICY,state_path=path)
    try:
        assert second.halted is True
        before=snapshot(restarted)
        trade=restarted.execute('BUY','100',timestamp=NOW+timedelta(minutes=1),
                                buy_fraction='.1',probability_up='.6')
        assert trade is None
        assert restarted.last_risk_outcome['status']=='halted'
        assert snapshot(restarted)==before
        with pytest.raises(ValueError):
            second.reset(actor='',reason='bad reset',timestamp=NOW+timedelta(minutes=2),
                         previous_forecast='.6')
        assert second.halted
        second.reset(actor='synthetic-operator',reason='Review complete',
                     timestamp=NOW+timedelta(minutes=3),previous_forecast='.6')
        events=second.audit_events()
        assert [e['event'] for e in events]==['initialize','halt','reset']
        assert events[-1]['actor']=='synthetic-operator'
        assert events[-1]['reason']=='Review complete'
    finally:
        second.close()

    third=RiskGateway(PaperTrader('10000',fee_rate=0,slippage_bps=0),POLICY,state_path=path)
    try:
        assert third.halted is False
        assert [e['event'] for e in third.audit_events()]==['initialize','halt','reset']
    finally:
        third.close()


def test_missing_state_and_existing_initialization_never_reset(tmp_path):
    path=tmp_path/'risk.sqlite3'
    trader=PaperTrader('10000')
    with pytest.raises(ValueError,match='explicitly initialized'):
        RiskGateway(trader,POLICY,state_path=path)
    assert not path.exists()
    initialize(path,trader)
    with pytest.raises(FileExistsError):
        initialize(path,trader)


def test_unconfigured_public_paper_entry_point_cannot_fill():
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    before=snapshot(trader)
    trade=trader.execute('BUY','100',buy_fraction='.1',timestamp=NOW,probability_up='.55')
    assert trade is None
    assert trader.last_risk_outcome['status']=='blocked'
    assert snapshot(trader)==before


def test_duplicate_and_conflicting_account_leave_all_balances_and_records_unchanged(tmp_path):
    path=tmp_path/'risk.sqlite3'
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    initialize(path,trader)
    other=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    first=RiskGateway(trader,POLICY,state_path=path)
    second=RiskGateway(other,POLICY,state_path=path)
    try:
        assert first.execute('BUY',probability_up='.55',btc_price_usd='100',
                             timestamp=NOW,buy_fraction='.1')['status']=='executed'
        before=snapshot(trader)
        blocked=first.execute('BUY',probability_up='.55',btc_price_usd='100',
                              timestamp=NOW,buy_fraction='.1')
        assert blocked['status']=='blocked' and 'Duplicate' in blocked['reason']
        assert snapshot(trader)==before
        other_before=snapshot(other)
        stale=second.execute('BUY',probability_up='.55',btc_price_usd='100',
                             timestamp=NOW+timedelta(minutes=1),buy_fraction='.1')
        assert stale['status']=='blocked'
        assert snapshot(other)==other_before
        assert trader.cash==Decimal('9000') and trader.btc==Decimal('10')
        assert len(trader.trades)==1
    finally:
        first.close()
        second.close()


def test_failed_state_write_rolls_back_paper_fill(tmp_path):
    path=tmp_path/'risk.sqlite3'
    trader=PaperTrader('10000',fee_rate=0,slippage_bps=0)
    initialize(path,trader)
    risk=RiskGateway(trader,POLICY,state_path=path)
    try:
        before=snapshot(trader)
        with patch.object(risk._store,'save',side_effect=OSError('synthetic failure')):
            outcome=risk.execute('BUY',probability_up='.55',btc_price_usd='100',
                                 timestamp=NOW,buy_fraction='.1')
        assert outcome['status']=='blocked'
        assert snapshot(trader)==before
        later=risk.execute('BUY',probability_up='.55',btc_price_usd='100',
                           timestamp=NOW+timedelta(minutes=1),buy_fraction='.1')
        assert later['status']=='blocked' and 'inspection' in later['reason']
        assert snapshot(trader)==before
    finally:
        risk.close()


def test_decision_cannot_predate_initialization(tmp_path):
    path=tmp_path/'risk.sqlite3'
    trader=PaperTrader('10000')
    initialize(path,trader)
    risk=RiskGateway(trader,POLICY,state_path=path)
    try:
        before=snapshot(trader)
        outcome=risk.execute('BUY',probability_up='.55',btc_price_usd='100',
                             timestamp=NOW-timedelta(minutes=2),buy_fraction='.1')
        assert outcome['status']=='blocked' and 'chronological' in outcome['reason']
        assert snapshot(trader)==before
    finally:
        risk.close()


def test_loss_halt_and_reset_survive_fresh_process_with_prior_trade(tmp_path):
    import sqlite3
    import subprocess
    import sys
    from risk.breaker_store import account_state

    path = tmp_path / 'risk.sqlite3'
    trader = PaperTrader('10000', fee_rate=0, slippage_bps=0)
    initialize(path, trader)
    risk = RiskGateway(trader, POLICY, state_path=path)
    try:
        assert risk.execute('BUY', probability_up='.5', btc_price_usd='100',
                            timestamp=NOW, buy_fraction='.2')['status'] == 'executed'
        halted = risk.execute('SELL', probability_up='.5', btc_price_usd='50',
                              timestamp=NOW + timedelta(minutes=1))
        assert halted['status'] == 'halted' and 'loss' in halted['reason']
        before = snapshot(trader)
    finally:
        risk.close()

    # Restart requires the account and ledger separately from the risk state.
    account = tmp_path / 'account.json'
    account.write_text(account_state(trader), encoding='utf-8')
    program = '''
import json, sys
from datetime import datetime, timezone
from decimal import Decimal
from execution.paper_trading import PaperTrader, Trade
from risk.durable_gateway import RiskGateway, RiskPolicy
with open(sys.argv[2], encoding="utf-8") as f:
    data = json.load(f)
trader = PaperTrader(data["initial_cash"], fee_rate=data["fee_rate"],
                     slippage_bps=Decimal(data["slippage"]) * 10000)
for field in ("cash", "btc", "cost_basis", "realized_pnl", "total_fees"):
    setattr(trader, field, Decimal(data[field]))
trader.trades = [Trade(**trade) for trade in data["trades"]]
risk = RiskGateway(trader, RiskPolicy(".2", ".1"), state_path=sys.argv[1])
try:
    assert risk.halted
    blocked = trader.execute("SELL", "50", probability_up=".5",
                             timestamp=datetime(2026, 11, 13, 12, 2, tzinfo=timezone.utc))
    assert blocked is None and trader.last_risk_outcome["status"] == "halted"
    risk.reset(actor="restart-operator", reason="Reviewed loss halt",
               timestamp=datetime(2026, 11, 13, 12, 3, tzinfo=timezone.utc),
               previous_forecast=".5")
    # Reset does not waive a still-breached loss threshold.
    result = risk.execute("SELL", probability_up=".5", btc_price_usd="50",
                          timestamp=datetime(2026, 11, 13, 12, 4, tzinfo=timezone.utc))
    assert result["status"] == "halted" and "loss" in result["reason"]
    assert [e["event"] for e in risk.audit_events()] == ["initialize", "halt", "reset", "halt"]
finally:
    risk.close()
'''
    subprocess.run([sys.executable, '-c', program, str(path), str(account)], check=True)
    assert snapshot(trader) == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT halted FROM state').fetchone() == (1,)
        assert db.execute("SELECT actor, reason FROM audit WHERE event='reset'").fetchone() == (
            'restart-operator', 'Reviewed loss halt')

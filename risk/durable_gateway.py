"""Risk-gated execution for the existing long-only BTC paper trader.

The 25% limit concerns BTC marked portfolio exposure, not agent influence.
Breaker limits are explicitly supplied by the caller; no team policy is guessed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from threading import RLock

from execution.paper_trading import PaperTrader, SATOSHI, decimal_value
from risk.breaker_store import BreakerStore, account_state

MAX_BTC_EXPOSURE = Decimal('0.25')


def _probability(value, name):
    number = decimal_value(value, name)
    if number > 1:
        raise ValueError(f'{name} must be between 0 and 1')
    return number


def _aware_time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('A timezone-aware timestamp is required')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RiskPolicy:
    """Caller-set absolute forecast swing and loss from initial equity.

    Both limits are fractions in (0, 1]; a value reaching a limit trips the
    breaker. The fixed exposure cap is not an agent weight or a loss limit.
    """
    max_forecast_swing: Decimal
    max_cumulative_loss: Decimal

    def __post_init__(self):
        for name in ('max_forecast_swing', 'max_cumulative_loss'):
            number = decimal_value(getattr(self, name), name, positive=True)
            if number > 1:
                raise ValueError(f'{name} must be in (0, 1]')
            object.__setattr__(self, name, number)


class RiskGateway:
    """The integrated paper-execution path; the wrapped trader is long-only.

    State must be explicitly initialized and persists across restarts. A breaker
    cannot be automatically reset. Missing or invalid state fails closed.
    """
    def __init__(self, trader: PaperTrader, policy: RiskPolicy, *, state_path, previous_forecast=None):
        if type(trader) is not PaperTrader or not isinstance(policy, RiskPolicy):
            raise ValueError('A paper trader and explicit risk policy are required')
        self._trader = trader
        self.policy = policy
        self._store = BreakerStore(state_path)
        self._risk_token = object()
        self.previous_forecast = None
        self.halted = False
        self.halt_reason = None
        self._last_time = None
        self._last_order_time = None
        self._storage_failed = False
        self._lock = RLock()
        try:
            row=self._store.begin()
            self._load_state(row)
            if previous_forecast is not None and _probability(previous_forecast,'previous forecast') != self.previous_forecast:
                raise ValueError('Previous forecast differs from durable state')
            if row[8] != account_state(trader) and not self.halted:
                raise ValueError('Paper account does not match durable risk state')
            self._store.commit()
            trader._bind_risk_gateway(self,self._risk_token)
        except Exception:
            self._store.rollback();self._store.close()
            raise

    @staticmethod
    def initialize_state(path, *, policy, trader, actor, reason, timestamp, previous_forecast=None):
        if not isinstance(policy,RiskPolicy) or type(trader) is not PaperTrader:
            raise ValueError('A paper trader and explicit risk policy are required')
        BreakerStore.initialize(path,policy=policy,trader=trader,actor=actor,reason=reason,
                                timestamp=timestamp,previous_forecast=previous_forecast)

    def _load_state(self,row):
        if Decimal(row[6]) != self.policy.max_forecast_swing or Decimal(row[7]) != self.policy.max_cumulative_loss:
            raise ValueError('Risk policy differs from durable state')
        if row[1] and (not isinstance(row[2],str) or not row[2].strip()):
            raise ValueError('Halted state lacks a reason')
        if not row[1] and row[2] is not None:
            raise ValueError('Unhalted state contains an inconsistent reason')
        self.halted=bool(row[1]);self.halt_reason=row[2]
        self.previous_forecast=None if row[3] is None else _probability(row[3],'previous forecast')
        self._last_time=None if row[4] is None else _aware_time(datetime.fromisoformat(row[4]))
        self._last_order_time=None if row[5] is None else _aware_time(datetime.fromisoformat(row[5]))

    def _save_state(self):
        self._store.save(halted=self.halted,halt_reason=self.halt_reason,previous_forecast=self.previous_forecast,
                         last_time=self._last_time,last_order_time=self._last_order_time,trader=self._trader)

    def reset(self, *, actor, reason, timestamp, previous_forecast):
        """Explicitly and durably reset a halt, with a caller-attested audit record."""
        when=_aware_time(timestamp);baseline=_probability(previous_forecast,'previous forecast')
        with self._lock:
            if self._storage_failed:
                raise ValueError('Durable state failure requires operator inspection')
            try:
                row=self._store.begin();self._load_state(row)
                if row[8]!=account_state(self._trader):raise ValueError('Paper account does not match durable risk state')
                if not self.halted:raise ValueError('No halted circuit breaker to reset')
                if self._last_time is not None and when<self._last_time:raise ValueError('Reset timestamp precedes the halt')
                self._store.audit('reset',when.isoformat(),actor,reason)
                self.halted=False;self.halt_reason=None;self.previous_forecast=baseline;self._last_time=when
                self._save_state();self._store.commit()
            except Exception:
                self._store.rollback()
                self.halted=True
                self.halt_reason='Risk reset failed; state requires inspection'
                raise

    def audit_events(self):return self._store.events()
    def close(self):self._store.close()

    def execute(self, action, *, probability_up=None, btc_price_usd=None, timestamp=None,
                buy_fraction=None, explanation=None):
        """Serialize checks and fill with persistent state; fail closed on errors."""
        with self._lock:
            if self._storage_failed:
                outcome={'status':'blocked','reason':'Durable state failure requires operator inspection',
                         'timestamp':None,'probability_up':None,'trade':None,'breaker_halted':True}
                self._trader.last_risk_outcome=outcome
                return outcome
            fields=('cash','btc','cost_basis','realized_pnl','total_fees')
            before={name:getattr(self._trader,name) for name in fields}
            trades_before=list(self._trader.trades)
            try:
                row=self._store.begin();self._load_state(row)
                was_halted=self.halted
                if row[8]!=account_state(self._trader):
                    if self.halted:
                        self._store.commit()
                        outcome={'status':'halted','reason':self.halt_reason,'timestamp':None,
                                 'probability_up':None,'trade':None,'breaker_halted':True}
                        self._trader.last_risk_outcome=outcome
                        return outcome
                    raise ValueError('Paper account does not match durable risk state')
                outcome=self._execute_checked(action,probability_up=probability_up,btc_price_usd=btc_price_usd,
                                              timestamp=timestamp,buy_fraction=buy_fraction,explanation=explanation)
                if self.halted and not was_halted:
                    self._store.audit('halt',outcome['timestamp'],'system',self.halt_reason)
                if outcome.get('trade') is not None:
                    self._last_order_time=_aware_time(datetime.fromisoformat(outcome['trade']['timestamp']))
                self._save_state();self._store.commit()
                self._trader.last_risk_outcome=outcome
                return outcome
            except Exception:
                self._storage_failed=True
                for name,value in before.items():setattr(self._trader,name,value)
                self._trader.trades[:]=trades_before
                self._store.rollback()
                outcome={'status':'blocked','reason':'Durable risk state unavailable or account mismatch',
                         'timestamp':None,'probability_up':None,'trade':None,'breaker_halted':True}
                self._trader.last_risk_outcome=outcome
                return outcome

    def _execute_checked(self, action, *, probability_up=None, btc_price_usd=None, timestamp=None,
                buy_fraction=None, explanation=None):
        """Validate inputs, enforce breaker/cap, and only then call paper execution."""
        with self._lock:
            record = {'status': 'blocked', 'reason': None, 'action': action,
                      'timestamp': None, 'probability_up': None, 'explanation': explanation,
                      'btc_exposure_before': None, 'btc_exposure_after': None,
                      'projected_btc_exposure': None,
                      'exposure_cap': str(MAX_BTC_EXPOSURE), 'cumulative_loss_fraction': None,
                      'forecast_swing': None, 'breaker_halted': self.halted, 'trade': None}

            def stop(reason, status='blocked'):
                record.update(status=status, reason=reason, breaker_halted=self.halted)
                return record

            if self.halted:
                if isinstance(timestamp, datetime) and timestamp.tzinfo is not None and timestamp.utcoffset() is not None:
                    record['timestamp'] = timestamp.astimezone(timezone.utc).isoformat()
                try:
                    record['probability_up'] = float(_probability(probability_up, 'forecast'))
                except (ValueError, TypeError, OverflowError, InvalidOperation):
                    pass
                return stop(self.halt_reason or 'Circuit breaker is halted', 'halted')
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                return stop('A timezone-aware decision timestamp is required')
            when = timestamp.astimezone(timezone.utc)
            record['timestamp'] = when.isoformat()
            if self._last_time is not None and when < self._last_time:
                return stop('Decision timestamps must be chronological')
            if action in ('BUY','SELL') and self._last_order_time is not None and when <= self._last_order_time:
                return stop('Duplicate or earlier paper order timestamp')
            try:
                forecast = _probability(probability_up, 'forecast')
            except (ValueError, TypeError, OverflowError, InvalidOperation):
                forecast = None
            if forecast is not None:
                record['probability_up'] = float(forecast)
                if self.previous_forecast is not None:
                    swing = abs(forecast - self.previous_forecast)
                    record['forecast_swing'] = str(swing)
                    if swing >= self.policy.max_forecast_swing:
                        self.halted = True
                        self.halt_reason = 'Circuit breaker: forecast swing reached the configured limit'
                        self._last_time = when
                        return stop(self.halt_reason, 'halted')
            try:
                price = decimal_value(btc_price_usd, 'BTC price', positive=True)
                cash = decimal_value(self._trader.cash, 'paper cash')
                btc = decimal_value(self._trader.btc, 'paper BTC')
                initial = decimal_value(self._trader.initial_cash, 'initial equity', positive=True)
                fee = decimal_value(self._trader.fee_rate, 'fee rate')
                slip = decimal_value(self._trader.slippage, 'slippage')
                if fee >= 1 or slip >= 1:
                    raise ValueError('Invalid paper costs')
            except (ValueError, TypeError, OverflowError, InvalidOperation):
                return stop('Missing or invalid price or account inputs')
            equity = cash + btc * price
            loss = max(Decimal('0'), (initial - equity) / initial)
            record['cumulative_loss_fraction'] = str(loss)
            if equity > 0:
                record['btc_exposure_before'] = str(btc * price / equity)
            if loss >= self.policy.max_cumulative_loss:
                self.halted = True
                self.halt_reason = 'Circuit breaker: cumulative loss reached the configured limit'
                self._last_time = when
                return stop(self.halt_reason, 'halted')
            if equity <= 0:
                return stop('Portfolio equity must be positive')
            if forecast is None:
                return stop('Missing or invalid forecast; no forecast was substituted')
            if self.previous_forecast is None:
                return stop('A previous forecast is required to assess forecast swing')
            if action not in ('BUY', 'SELL', 'HOLD'):
                return stop('Missing or invalid paper action')
            if btc * price / equity > MAX_BTC_EXPOSURE and action != 'SELL':
                self.previous_forecast = forecast
                self._last_time = when
                return stop('Existing BTC exposure exceeds 25%; reduction is required')
            fraction = Decimal('1')
            if action == 'BUY':
                try:
                    fraction = decimal_value(buy_fraction, 'buy fraction', positive=True)
                    if fraction > 1:
                        raise ValueError('buy fraction exceeds one')
                    execution_price = price * (1 + slip)
                    quantity = (cash * fraction / (execution_price * (1 + fee))).quantize(SATOSHI, rounding=ROUND_DOWN)
                    cash_after = cash - quantity * execution_price * (1 + fee)
                    btc_after = btc + quantity
                    equity_after = cash_after + btc_after * price
                    if equity_after <= 0:
                        return stop('Projected portfolio equity must be positive')
                    exposure_after = btc_after * price / equity_after
                except (ValueError, TypeError, OverflowError, InvalidOperation, ZeroDivisionError):
                    return stop('Missing or invalid buy size or projected account values')
                record['projected_btc_exposure'] = str(exposure_after)
                if exposure_after > MAX_BTC_EXPOSURE:
                    record['btc_exposure_after'] = record['btc_exposure_before']
                    self.previous_forecast = forecast
                    self._last_time = when
                    return stop('Blocked: projected BTC exposure exceeds 25%')
            try:
                trade = self._trader._execute_approved(action, price, buy_fraction=fraction, timestamp=when,
                                                       _risk_token=self._risk_token)
            except (ValueError, TypeError, OverflowError, InvalidOperation):
                return stop('Paper execution rejected the order')
            self.previous_forecast = forecast
            self._last_time = when
            updated_equity = self._trader.cash + self._trader.btc * price
            record['btc_exposure_after'] = str(self._trader.btc * price / updated_equity) if updated_equity else None
            if trade is None:
                return stop('No paper trade was executed', 'no_trade')
            record['trade'] = asdict(trade)
            return stop('Risk checks passed; paper trade executed', 'executed')

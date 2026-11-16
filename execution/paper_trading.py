"""Long-only BTC/USD paper execution with explicit costs and a trade ledger."""

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

ZERO = Decimal("0")
SATOSHI = Decimal("0.00000001")


def decimal_value(value, name, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


@dataclass(frozen=True)
class Trade:
    timestamp: str
    action: str
    quantity_btc: str
    market_price_usd: str
    execution_price_usd: str
    fee_usd: str
    cash_after_usd: str
    btc_after: str
    realized_pnl_usd: str


class PaperTrader:
    """No exchange connection. Caller supplies the final ensemble signal.

    BUY spends a fraction of available cash including fees; SELL closes the
    entire BTC holding. Position limits belong to the team's risk engine.
    """

    def __init__(self, initial_cash="10000", *, fee_rate="0.001", slippage_bps="5"):
        self.initial_cash = decimal_value(initial_cash, "initial cash", positive=True)
        self.cash = self.initial_cash
        self.fee_rate = decimal_value(fee_rate, "fee rate")
        self.slippage = decimal_value(slippage_bps, "slippage bps") / Decimal("10000")
        if self.fee_rate >= 1 or self.slippage >= 1:
            raise ValueError("Fee and slippage rates must be less than 100%")
        self.btc = ZERO
        self.cost_basis = ZERO
        self.realized_pnl = ZERO
        self.total_fees = ZERO
        self.trades: list[Trade] = []
        self._risk_gateway = None
        self.__risk_token = None
        self.last_risk_outcome = None

    def _bind_risk_gateway(self, gateway, token):
        from risk.durable_gateway import RiskGateway
        if type(gateway) is not RiskGateway or gateway._trader is not self or token is None or self._risk_gateway is not None:
            raise ValueError('A paper account may be bound to one risk gateway')
        self._risk_gateway, self.__risk_token = gateway, token

    def execute(self, action: str, btc_price_usd, *, buy_fraction="1", timestamp=None,
                probability_up=None, explanation=None) -> Trade | None:
        """Submit one paper request through a configured durable risk gateway."""
        when=timestamp or datetime.now(timezone.utc)
        # Preserve the public API's validation errors, but still let a configured
        # gateway evaluate and persist breaker triggers before reporting them.
        # An invalid request must never be allowed to fill in that evaluation.
        invalid = None
        try:
            if action not in {'BUY', 'SELL', 'HOLD'}:
                raise ValueError('action must be BUY, SELL, or HOLD')
            decimal_value(btc_price_usd, 'BTC price', positive=True)
            fraction = decimal_value(buy_fraction, 'buy fraction', positive=True)
            if fraction > 1:
                raise ValueError('buy fraction must be in (0, 1]')
            if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
                raise ValueError('timestamp must be a timezone-aware datetime')
            if self.trades and when < datetime.fromisoformat(self.trades[-1].timestamp):
                raise ValueError('Trades must be in chronological order')
        except ValueError as exc:
            invalid = exc
        if self._risk_gateway is None:
            self.last_risk_outcome = {'status':'blocked','reason':'Explicit durable risk gateway is required','trade':None}
            if invalid is not None:
                raise invalid
            return None
        outcome=self._risk_gateway.execute(action if invalid is None else None,probability_up=probability_up,btc_price_usd=btc_price_usd,
                                           timestamp=when,buy_fraction=buy_fraction,explanation=explanation)
        self.last_risk_outcome=outcome
        if invalid is not None:
            raise invalid
        return Trade(**outcome['trade']) if outcome.get('status')=='executed' and outcome.get('trade') else None

    def _execute_approved(self, action: str, btc_price_usd, *, buy_fraction="1", timestamp=None,
                          _risk_token=None) -> Trade | None:
        if self.__risk_token is None or _risk_token is not self.__risk_token:
            raise PermissionError('Only the bound risk gateway may execute a paper order')
        if action not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("action must be BUY, SELL, or HOLD")
        price = decimal_value(btc_price_usd, "BTC price", positive=True)
        fraction = decimal_value(buy_fraction, "buy fraction", positive=True)
        if fraction > 1:
            raise ValueError("buy fraction must be in (0, 1]")
        when = timestamp or datetime.now(timezone.utc)
        if not isinstance(when, datetime) or when.tzinfo is None:
            raise ValueError("timestamp must be a timezone-aware datetime")
        if self.trades and when < datetime.fromisoformat(self.trades[-1].timestamp):
            raise ValueError("Trades must be in chronological order")
        if action == "HOLD" or (action == "SELL" and self.btc == 0):
            return None
        realized = ZERO
        if action == "BUY":
            execution_price = price * (1 + self.slippage)
            quantity = (self.cash * fraction / (execution_price * (1 + self.fee_rate))).quantize(
                SATOSHI, rounding=ROUND_DOWN,
            )
            if quantity == 0:
                return None
            notional = quantity * execution_price
            fee = notional * self.fee_rate
            self.cash -= notional + fee
            self.btc += quantity
            self.cost_basis += notional + fee
        else:
            execution_price = price * (1 - self.slippage)
            quantity = self.btc
            notional = quantity * execution_price
            fee = notional * self.fee_rate
            proceeds = notional - fee
            realized = proceeds - self.cost_basis
            self.cash += proceeds
            self.btc = ZERO
            self.cost_basis = ZERO
            self.realized_pnl += realized
        self.total_fees += fee
        trade = Trade(when.astimezone(timezone.utc).isoformat(), action, str(quantity), str(price),
                      str(execution_price), str(fee), str(self.cash), str(self.btc), str(realized))
        self.trades.append(trade)
        return trade

    def summary(self, btc_price_usd) -> dict:
        price = decimal_value(btc_price_usd, "BTC price", positive=True)
        market_value = self.btc * price
        equity = self.cash + market_value
        return {"initial_cash_usd": str(self.initial_cash), "cash_usd": str(self.cash),
                "btc": str(self.btc), "btc_price_usd": str(price),
                "equity_usd": str(equity), "cost_basis_usd": str(self.cost_basis),
                "realized_pnl_usd": str(self.realized_pnl),
                "unrealized_pnl_usd": str(market_value - self.cost_basis),
                "total_pnl_usd": str(equity - self.initial_cash),
                "return_pct": str((equity / self.initial_cash - 1) * 100),
                "total_fees_usd": str(self.total_fees), "trade_count": len(self.trades)}

    def export(self, directory, btc_price_usd):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(Trade.__dataclass_fields__))
            writer.writeheader()
            writer.writerows(asdict(trade) for trade in self.trades)
        (directory / "portfolio.json").write_text(
            json.dumps(self.summary(btc_price_usd), indent=2) + "\n", encoding="utf-8",
        )

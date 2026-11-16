"""Enforce a 25% BTC/equity ceiling before Student 4 paper execution.

The cap applies to total holdings, not 25% of cash on each BUY. Holdings are
marked at the supplied BTC price; cash includes adverse slippage and fees.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

CAP = Decimal("0.25")
SATOSHI = Decimal("0.00000001")
ZERO = Decimal("0")


def _number(value, name, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError(name)
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


@dataclass(frozen=True)
class RiskDecision:
    action: str
    buy_fraction: Decimal
    exposure_before: Decimal
    projected_exposure: Decimal
    reason: str

    def to_dict(self):
        return {key: str(value) if isinstance(value, Decimal) else value
                for key, value in self.__dict__.items()}


def approve_trade(action, *, cash_usd, btc, btc_price_usd, requested_buy_fraction="1",
                  fee_rate="0.001", slippage_bps="5"):
    """Return a constrained action; invalid inputs raise before any trade.

    An already-overweight account is fully liquidated even on HOLD/BUY because
    the existing PaperTrader supports full SELL only. No partial-sell assumption.
    This enforces the limit at decision/execution checks, not between price ticks.
    """
    if action not in {"BUY", "SELL", "HOLD"}:
        raise ValueError("action must be BUY, SELL, or HOLD")
    cash = _number(cash_usd, "cash")
    holding = _number(btc, "btc")
    price = _number(btc_price_usd, "price", positive=True)
    fraction = _number(requested_buy_fraction, "requested fraction")
    fee = _number(fee_rate, "fee")
    slippage = _number(slippage_bps, "slippage") / Decimal("10000")
    if fraction > 1 or fee >= 1 or slippage >= 1:
        raise ValueError("fraction must be <= 1; fee/slippage must be < 100%")
    btc_value = holding * price
    equity = cash + btc_value
    before = btc_value / equity if equity else ZERO
    if btc_value > CAP * equity:
        return RiskDecision("SELL", ZERO, before, ZERO,
                            "BTC exceeds 25%: full SELL required by current simulator")
    if action == "SELL":
        return RiskDecision("SELL" if holding else "HOLD", ZERO, before, ZERO,
                            "close BTC position" if holding else "no BTC to sell")
    if action == "HOLD" or not cash or not fraction:
        return RiskDecision("HOLD", ZERO, before, before, "no new BTC allocation")

    cost_per_btc = price * (1 + slippage) * (1 + fee)
    # q*price + existing_BTC <= CAP * (equity - q*(cost_per_btc-price))
    room = CAP * equity - btc_value
    cap_quantity = room / (price + CAP * (cost_per_btc - price))
    requested_quantity = cash * fraction / cost_per_btc
    quantity = min(cap_quantity, requested_quantity).quantize(SATOSHI, rounding=ROUND_DOWN)
    if quantity <= 0:
        return RiskDecision("HOLD", ZERO, before, before, "25% cap reached or order below one satoshi")
    # Round the cash fraction down too: paper execution must never round up.
    approved = (quantity * cost_per_btc / cash).quantize(Decimal("1e-24"), rounding=ROUND_DOWN)
    actual_quantity = (cash * approved / cost_per_btc).quantize(SATOSHI, rounding=ROUND_DOWN)
    if actual_quantity <= 0:
        return RiskDecision("HOLD", ZERO, before, before, "approved order below one satoshi")
    after_btc = btc_value + actual_quantity * price
    after_equity = equity - actual_quantity * (cost_per_btc - price)
    if after_btc > CAP * after_equity:
        raise ArithmeticError("could not size order safely within 25%")
    return RiskDecision("BUY", approved, before, after_btc / after_equity,
                        "BUY limited to 25% of post-cost portfolio equity")


def execute_guarded(trader, action, btc_price_usd, *, requested_buy_fraction="1", timestamp=None,
                    probability_up=None, explanation=None):
    """Submit the exact request once through the bound durable gateway.

    `approve_trade` remains available for legacy sizing previews, but paper
    execution must not resize a request or turn HOLD into a liquidation.
    """
    trade = trader.execute(action, btc_price_usd,
                           buy_fraction=requested_buy_fraction if action == "BUY" else "1",
                           timestamp=timestamp, probability_up=probability_up, explanation=explanation)
    outcome = trader.last_risk_outcome or {}
    reason = str(outcome.get("reason") or "No paper trade was executed")
    after = outcome.get("btc_exposure_after")
    before = outcome.get("btc_exposure_before")
    exposure_before = Decimal(before) if before is not None else ZERO
    exposure = Decimal(after) if after is not None else exposure_before
    decision = RiskDecision(action if trade is not None else "HOLD",
                            _number(requested_buy_fraction, "requested fraction") if trade is not None and action == "BUY" else ZERO,
                            exposure_before, exposure, reason)
    return decision, trade

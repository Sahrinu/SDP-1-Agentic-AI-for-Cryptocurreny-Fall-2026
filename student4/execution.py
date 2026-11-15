"""Optional BUY liquidity gate around Student 5's mandatory risk gateway."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from risk.gateway import approve_trade, execute_guarded
from .paper_trading import decimal_value


@dataclass(frozen=True)
class LiquidityQuote:
    bid: object
    ask: object
    ask_depth_btc: object
    observed_at: datetime


def execute_with_quality(trader, action, btc_price_usd, *, quote=None,
                         requested_buy_fraction="1", timestamp=None):
    """Return (risk decision, trade, quality reason).

    BUY requires fresh depth available at or below the simulator's adverse fill
    price. SELL/HOLD, including mandatory liquidation, retain gateway behavior.
    The caller must aggregate depth only up to that price, not the entire book.
    """
    when = timestamp or datetime.now(timezone.utc)
    decision = approve_trade(action, cash_usd=trader.cash, btc=trader.btc,
        btc_price_usd=btc_price_usd, requested_buy_fraction=requested_buy_fraction,
        fee_rate=trader.fee_rate, slippage_bps=trader.slippage * Decimal("10000"))
    if decision.action == "BUY":
        try:
            if quote is None or when.tzinfo is None or quote.observed_at.tzinfo is None:
                raise ValueError("Missing timestamped liquidity quote")
            if not 0 <= (when - quote.observed_at).total_seconds() <= 30:
                raise ValueError("Stale or future liquidity quote")
            bid = decimal_value(quote.bid, "bid", positive=True)
            ask = decimal_value(quote.ask, "ask", positive=True)
            depth = decimal_value(quote.ask_depth_btc, "depth", positive=True)
            price = decimal_value(btc_price_usd, "price", positive=True)
            fill = price * (1 + trader.slippage)
            quantity = trader.cash * decision.buy_fraction / (fill * (1 + trader.fee_rate))
            if bid > ask or not bid <= price <= ask or ask > fill or depth < quantity:
                raise ValueError("Spread or executable depth exceeds simulated cost budget")
        except (TypeError, ValueError, AttributeError) as exc:
            return decision, None, f"DEFER: {exc}"
    approved, trade = execute_guarded(trader, action, btc_price_usd,
        requested_buy_fraction=requested_buy_fraction, timestamp=when)
    return approved, trade, "Passed execution checks" if trade else "No trade required"

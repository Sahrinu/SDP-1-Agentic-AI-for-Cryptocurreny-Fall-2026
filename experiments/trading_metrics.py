"""Trading metrics from a paper account's daily equity curve and trade ledger.

Definitions (report Section 3.2.7):
- daily return r(t) = E(t) / E(t-1) - 1, with E(0) the starting cash, so the
  first marked day is measured against the money the account started with;
- cumulative return R = E(T) / E(0) - 1;
- Sharpe = sqrt(365) * mean(r) / sd(r), sample standard deviation, risk-free
  rate zero because the simulator pays no interest on cash; 365 because
  Bitcoin trades every day of the year;
- maximum drawdown = max over t of 1 - E(t) / max over s <= t of E(s),
  where the running peak includes the starting cash;
- turnover = total traded value (quantity x fill price, buys and sells) /
  mean marked equity; fees paid = sum of fees.
"""

import math

TRADING_DAYS = 365


def _float(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _field(row, name):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def trading_metrics(equity, trades, initial_cash):
    """Return the Section 3.2.7 metrics; Sharpe is None when it is undefined.

    `equity` is a sequence of daily marked equity values (or rows with an
    `equity_usd` field); `trades` are Trade objects or dicts from the ledger.
    """
    start = _float(initial_cash, "initial cash")
    if start <= 0:
        raise ValueError("initial cash must be positive")
    curve = [_float(_field(row, "equity_usd") if not isinstance(row, (int, float, str)) else row, "equity")
             for row in equity]
    if not curve:
        raise ValueError("equity curve is empty")
    if any(value <= 0 for value in curve):
        raise ValueError("equity must stay positive")
    path = [start] + curve
    returns = [b / a - 1 for a, b in zip(path, path[1:])]
    sharpe = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
        if sd > 1e-15:
            sharpe = math.sqrt(TRADING_DAYS) * mean / sd
    peak, drawdown = path[0], 0.0
    for value in path:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    traded = sum(_float(_field(t, "quantity_btc"), "quantity") * _float(_field(t, "execution_price_usd"), "fill price")
                 for t in trades)
    fees = sum(_float(_field(t, "fee_usd"), "fee") for t in trades)
    return {
        "days": len(curve),
        "cumulative_return_pct": (curve[-1] / start - 1) * 100,
        "sharpe_annualised": sharpe,
        "max_drawdown_pct": drawdown * 100,
        "turnover": traded / (sum(curve) / len(curve)),
        "traded_value_usd": traded,
        "fees_paid_usd": fees,
        "trades": len(trades),
    }

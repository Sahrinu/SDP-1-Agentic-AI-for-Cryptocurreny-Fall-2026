"""Replay combined forecasts through the risk gateway and paper execution.

Each method/scenario owns an independent account. A forecast for day D may
execute only on D+1, never at the close used to produce that forecast.
"""

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from risk.gateway import execute_guarded
from risk.circuit_breaker import CircuitBreaker, constrained_action
from execution.paper_trading import PaperTrader

METHODS = ("exchange", "vote", "average")


@dataclass(frozen=True)
class TradingPolicy:
    buy_threshold: float = 0.55
    sell_threshold: float = 0.45
    execution: str = "next_open"
    circuit_breaker: bool = False
    initial_cash: str = "10000"
    fee_rate: str = "0.001"
    slippage_bps: str = "5"

    def __post_init__(self):
        if not (0 <= self.sell_threshold < 0.5 < self.buy_threshold <= 1):
            raise ValueError("require 0 <= sell threshold < 0.5 < buy threshold <= 1")
        if self.execution not in {"next_open", "next_close"}:
            raise ValueError("execution must be next_open or next_close")
        PaperTrader(self.initial_cash, fee_rate=self.fee_rate, slippage_bps=self.slippage_bps)

    def action(self, probability):
        if probability is None or pd.isna(probability):
            return "HOLD"
        if isinstance(probability, bool):
            raise ValueError("probability cannot be boolean")
        p = float(probability)
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("probability must be finite and between zero and one")
        if p >= self.buy_threshold:
            return "BUY"
        if p <= self.sell_threshold:
            return "SELL"
        return "HOLD"


def _dates(values):
    return pd.to_datetime(values, utc=True).tz_convert(None).normalize()


def replay_portfolios(prices, forecasts, attacks, output, *, policy=None, synthetic=False):
    """Write replay accounts; output must be new/empty to protect old results.

    Historical OHLC must be daily UTC candles. next_close is an explicit delayed
    execution fallback for close-only data, not execution at the forecast close.
    Attacked accounts replace only comparable attack-date team forecasts. They
    share the clean Exchange's forecast/coin history (local-intervention study),
    not a separately retrained or attacked coin-settlement economy.
    """
    policy = policy or TradingPolicy()
    prices, forecasts = prices.copy(), forecasts.copy()
    prices.index = _dates(prices.index)
    if prices.empty or prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("prices must be nonempty, unique and chronological")
    if any((b - a).days != 1 for a, b in zip(prices.index, prices.index[1:])):
        raise ValueError("daily UTC BTC prices must be consecutive")
    execution_field = "open" if policy.execution == "next_open" else "close"
    for field in {"close", execution_field}:
        if field not in prices:
            raise ValueError(f"{policy.execution} requires BTC {field} prices; explicitly choose next_close for close-only inputs")
        prices[field] = pd.to_numeric(prices[field], errors="raise")
        if not all(math.isfinite(float(x)) and x > 0 for x in prices[field]):
            raise ValueError(f"BTC {field} prices must be positive and finite")
    if forecasts.empty or not {"date", *METHODS} <= set(forecasts):
        raise ValueError("need dated forecasts for Exchange, vote and average")
    forecasts.index = _dates(pd.DatetimeIndex(forecasts.pop("date")))
    if forecasts.index.has_duplicates or not forecasts.index.is_monotonic_increasing:
        raise ValueError("forecast dates must be unique and chronological")
    for day, row in forecasts.iterrows():
        if day not in prices.index or day + pd.Timedelta(days=1) not in prices.index:
            raise ValueError("every forecast requires its own BTC close and the following day's candle")
        for method in METHODS:
            policy.action(row[method])  # validate all before any output

    attack_lookup = {}
    if attacks is not None and not attacks.empty:
        for record in attacks.to_dict("records"):
            if record["method"] not in METHODS:
                raise ValueError("unknown attacked method")
            day = pd.Timestamp(record["date"]).normalize().tz_localize(None)
            key = (day, record["method"])
            if key in attack_lookup or day not in forecasts.index:
                raise ValueError("duplicate attack or attack outside forecast dates")
            comparable = record.get("comparable", True)
            if comparable not in (True, False):
                raise ValueError("attack comparable flag must be boolean")
            if comparable:
                policy.action(record["attacked"])
            attack_lookup[key] = record

    out = Path(output)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("paper output is not empty; choose a new directory")
    scenarios = ("clean", "attacked") if attack_lookup else ("clean",)
    summaries = []
    for scenario in scenarios:
        for method in METHODS:
            trader = PaperTrader(policy.initial_cash, fee_rate=policy.fee_rate,
                                 slippage_bps=policy.slippage_bps)
            decisions, equity_rows = [], []
            breaker = CircuitBreaker()
            first = forecasts.index[0]
            final = forecasts.index[-1] + pd.Timedelta(days=1)
            for day, candle in prices.loc[first:final].iterrows():
                decision_day = day - pd.Timedelta(days=1)
                if decision_day in forecasts.index:
                    probability = forecasts.loc[decision_day, method]
                    attack = attack_lookup.get((decision_day, method)) if scenario == "attacked" else None
                    comparable = attack is None or bool(attack.get("comparable", True))
                    if attack is not None:
                        # Do not silently call a missing attacked result a clean result.
                        probability = attack["attacked"] if comparable else None
                    action = policy.action(probability)
                    requested = action
                    status = {"halted":False,"reason":"disabled for this replay policy"}
                    if policy.circuit_breaker:
                        equity = trader.summary(str(candle[execution_field]))["equity_usd"]
                        available = probability is not None and not pd.isna(probability)
                        status = breaker.check(equity, probability if available else None)
                        participants = forecasts.loc[decision_day].get("available_agents", 0)
                        if attack is not None:
                            participants = len(json.loads(attack.get("attacked_active_agents", "[]")))
                        action = constrained_action(action,halted=status["halted"],
                            audit_passed=available and comparable,cap_feasible=participants >= 5)
                    # A daily open is at 00:00 UTC; a close is at the following
                    # midnight. The supplied slippage models adverse execution.
                    when = day if execution_field == "open" else day + pd.Timedelta(days=1)
                    approval, trade = execute_guarded(
                        trader, action, str(candle[execution_field]), timestamp=when.tz_localize("UTC").to_pydatetime(),
                    )
                    decisions.append({
                        "decision_date": str(decision_day.date()), "execution_date": str(day.date()),
                        "execution_time": when.tz_localize("UTC").isoformat(),
                        "probability": None if pd.isna(probability) else probability,
                        "requested_action": requested, "constrained_action": action,
                        "breaker_halted": status["halted"], "breaker_reason": status["reason"], "attacked_day": attack is not None,
                        "comparable_attack": comparable, "forecast_available": probability is not None and not pd.isna(probability),
                        "quote": float(candle[execution_field]), "traded": trade is not None,
                        **approval.to_dict(),
                    })
                account = trader.summary(str(candle["close"]))
                exposure = trader.btc * Decimal(str(candle["close"])) / Decimal(account["equity_usd"])
                equity_rows.append({"date": str(day.date()), **account, "btc_exposure": str(exposure),
                                    "over_cap_at_close": exposure > Decimal("0.25")})
            folder = out / scenario / method
            trader.export(folder, str(prices.loc[final, "close"]))
            pd.DataFrame(decisions).to_csv(folder / "risk_decisions.csv", index=False)
            pd.DataFrame(equity_rows).to_csv(folder / "equity.csv", index=False)
            summaries.append({"scenario": scenario, "method": method,
                              "missing_forecast_days": sum(not x["forecast_available"] for x in decisions),
                              **trader.summary(str(prices.loc[final, "close"]))})
    pd.DataFrame(summaries).to_csv(out / "summary.csv", index=False)
    (out / "run.json").write_text(json.dumps({
        "synthetic": synthetic, "policy": policy.__dict__,
        "forecast_start": str(forecasts.index[0].date()),
        "forecast_end": str(forecasts.index[-1].date()),
        "attack_design": "local interventions using clean forecast and coin history",
        "state": "fresh independent accounts per replay; exports do not resume a live account",
        "risk": "25% checked at execution quotes; intraday price drift can exceed it between checks",
    }, indent=2) + "\n", encoding="utf-8")
    return pd.DataFrame(summaries)

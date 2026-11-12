"""Explainable prototype signals for the team's BTC market engine."""

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .data import DataError, StablecoinSnapshot


@dataclass(frozen=True)
class AgentSignal:
    agent: str
    action: str
    score: int
    reason: str
    metrics: dict

    def to_dict(self) -> dict:
        return asdict(self)


class StablecoinAgent:
    """Peg stress overrides the optional market-cap liquidity heuristic.

    Scores use -1=SELL, 0=HOLD, +1=BUY for majority vote or simple average.
    These rules are classroom heuristics, not calibrated forecasts.
    """

    def __init__(self, warning_pct=0.5, depeg_pct=2.0, cap_change_pct=1.0):
        if not all(math.isfinite(v) for v in (warning_pct, depeg_pct, cap_change_pct)):
            raise ValueError("Thresholds must be finite")
        if not 0 < warning_pct < depeg_pct or cap_change_pct <= 0:
            raise ValueError("Require 0 < warning < depeg and a positive cap-change threshold")
        self.warning_pct = warning_pct
        self.depeg_pct = depeg_pct
        self.cap_change_pct = cap_change_pct

    def analyze(self, current: StablecoinSnapshot, previous: StablecoinSnapshot | None = None,
                *, now: datetime | None = None) -> AgentSignal:
        metrics = {}

        def signal(action, reason):
            return AgentSignal("stablecoin", action, {"SELL": -1, "HOLD": 0, "BUY": 1}[action], reason, metrics)

        try:
            current.validate_freshness(now or datetime.now(timezone.utc))
        except DataError as exc:
            return signal("HOLD", f"Data unavailable for a fresh decision: {exc}")
        metrics.update({
            "source": current.source,
            "observed_at": current.observed_at.isoformat(),
            "total_market_cap_usd": current.total_market_cap_usd,
            "peg_deviation_pct": {q.symbol: round(q.peg_deviation_pct, 6) for q in current.quotes},
        })
        worst = max(current.quotes, key=lambda q: q.peg_deviation_pct)
        if worst.peg_deviation_pct >= self.depeg_pct:
            return signal("SELL", f"Peg stress: {worst.symbol} deviates {worst.peg_deviation_pct:.2f}% from $1")
        if worst.peg_deviation_pct >= self.warning_pct:
            return signal("HOLD", f"Peg warning: {worst.symbol} deviates {worst.peg_deviation_pct:.2f}% from $1")
        if previous is None:
            return signal("HOLD", "Pegs are stable; an earlier snapshot is needed to compare liquidity")
        try:
            previous.validate_freshness(previous.observed_at)
            elapsed = (current.observed_at - previous.observed_at).total_seconds()
            if elapsed <= 0 or previous.source != current.source:
                raise DataError("Baseline must be earlier and from the same source")
            if any(q.updated_at <= old.updated_at for q, old in zip(
                sorted(current.quotes, key=lambda q: q.coin_id),
                sorted(previous.quotes, key=lambda q: q.coin_id),
            )):
                raise DataError("Coin quotes must be newer than the baseline")
        except DataError as exc:
            return signal("HOLD", f"Cannot compare liquidity: {exc}")
        change = (current.total_market_cap_usd / previous.total_market_cap_usd - 1) * 100
        metrics.update({"market_cap_change_pct": change, "comparison_hours": elapsed / 3600})
        if change >= self.cap_change_pct - 1e-9:
            return signal("BUY", f"Stablecoin market cap grew {change:.2f}% over {elapsed / 3600:.2f} hours; supportive liquidity proxy")
        if change <= -self.cap_change_pct + 1e-9:
            return signal("SELL", f"Stablecoin market cap fell {abs(change):.2f}% over {elapsed / 3600:.2f} hours; weaker liquidity proxy")
        return signal("HOLD", f"Pegs are stable and market-cap change is small ({change:+.2f}%)")

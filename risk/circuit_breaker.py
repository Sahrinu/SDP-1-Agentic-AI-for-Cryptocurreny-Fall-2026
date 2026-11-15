"""Persistent-in-session trading pause with explicit reset and audit reasons."""

from dataclasses import dataclass
import math


@dataclass
class CircuitBreaker:
    max_drawdown: float = 0.10
    max_forecast_jump: float = 0.25
    peak_equity: float | None = None
    previous_forecast: float | None = None
    halted: bool = False
    reason: str = ""

    def check(self, equity, probability):
        if not 0 < self.max_drawdown < 1 or not 0 < self.max_forecast_jump <= 1:
            raise ValueError("invalid circuit breaker thresholds")
        equity = float(equity)
        if not math.isfinite(equity) or equity <= 0:
            self.halted, self.reason = True, "Invalid or depleted account equity"
        else:
            self.peak_equity = max(self.peak_equity or equity, equity)
            if 1 - equity / self.peak_equity >= self.max_drawdown:
                self.halted, self.reason = True, "Drawdown threshold reached"
        if probability is not None:
            probability = float(probability)
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                self.halted, self.reason = True, "Invalid team probability"
            elif self.previous_forecast is not None and abs(probability - self.previous_forecast) > self.max_forecast_jump:
                self.halted, self.reason = True, "Team forecast changed beyond the configured limit"
            self.previous_forecast = probability
        return {"halted": self.halted, "reason": self.reason,
                "peak_equity": self.peak_equity, "max_drawdown": self.max_drawdown,
                "max_forecast_jump": self.max_forecast_jump}

    def reset(self):
        self.halted = False
        self.reason = ""
        self.peak_equity = None
        self.previous_forecast = None


def constrained_action(requested, *, halted=False, audit_passed=True, cap_feasible=True):
    # Risk-reducing SELL and mandatory gateway liquidations remain possible.
    return "HOLD" if requested == "BUY" and (halted or not audit_passed or not cap_feasible) else requested

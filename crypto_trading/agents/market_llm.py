"""Provider-independent language-model step for interpreting market evidence."""

import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Callable

from crypto_trading.agents.market import MarketReport


@dataclass(frozen=True)
class MarketInterpretation:
    summary: str
    trend_analysis: str
    momentum_analysis: str
    volatility_analysis: str
    volume_analysis: str
    key_observations: list[str]
    limitations: list[str]
    confidence: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_market_prompt(report: MarketReport) -> str:
    """Give a model only verified, timestamped inputs and a narrow task."""
    facts = {
        "symbol": report.symbol,
        "interval": report.interval,
        "as_of_utc": report.as_of_utc,
        "source": report.source,
        "computed_observations": {
            "trend": report.trend,
            "momentum": report.momentum,
            "volatility": report.volatility,
            "participation": report.participation,
        },
        "indicator_values": report.evidence,
        "limitations": report.limitations,
    }
    instructions = files("crypto_trading.agents.prompts").joinpath("market.txt").read_text(encoding="utf-8")
    return instructions + "\n\nMARKET_DATA_JSON:\n" + json.dumps(facts, separators=(",", ":"))


def parse_market_interpretation(raw: str) -> MarketInterpretation:
    """Reject malformed model responses before they enter the decision workflow."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("market model did not return valid JSON") from exc
    expected = {"summary", "trend_analysis", "momentum_analysis", "volatility_analysis",
                "volume_analysis", "key_observations", "limitations", "confidence"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("market model returned unexpected fields")
    for key in ("summary", "trend_analysis", "momentum_analysis",
                "volatility_analysis", "volume_analysis"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"market model {key} is missing")
    if not isinstance(data["confidence"], str) or data["confidence"] not in {"low", "medium", "high"}:
        raise ValueError("market model confidence is invalid")
    for key in ("key_observations", "limitations"):
        if (not isinstance(data[key], list)
                or any(not isinstance(item, str) or not item.strip() for item in data[key])):
            raise ValueError(f"market model {key} must be a list of nonempty strings")
    return MarketInterpretation(**data)


def interpret_market(report: MarketReport, model: Callable[[str], str]) -> MarketInterpretation:
    """Call any configured model adapter that accepts a prompt and returns text."""
    return parse_market_interpretation(model(build_market_prompt(report)))

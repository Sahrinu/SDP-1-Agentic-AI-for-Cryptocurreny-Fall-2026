"""Provider-independent LLM interpretation of on-chain evidence."""

import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Callable

from crypto_trading.agents.fundamentals import FundamentalsReport


@dataclass(frozen=True)
class FundamentalsInterpretation:
    summary: str
    network_analysis: str
    mining_analysis: str
    key_observations: list[str]
    limitations: list[str]
    confidence: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_fundamentals_prompt(report: FundamentalsReport) -> str:
    instructions = files("crypto_trading.agents.prompts").joinpath("fundamentals.txt").read_text(encoding="utf-8")
    return instructions + "\n\nFUNDAMENTALS_DATA_JSON:\n" + json.dumps(report.to_dict(), separators=(",", ":"))


def parse_fundamentals_interpretation(raw: str) -> FundamentalsInterpretation:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("fundamentals model did not return valid JSON") from exc
    expected = {"summary", "network_analysis", "mining_analysis", "key_observations",
                "limitations", "confidence"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("fundamentals model returned unexpected fields")
    for key in ("summary", "network_analysis", "mining_analysis"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"fundamentals model {key} is missing")
    if data["confidence"] not in {"low", "medium", "high"}:
        raise ValueError("fundamentals model confidence is invalid")
    for key in ("key_observations", "limitations"):
        if not isinstance(data[key], list) or any(not isinstance(item, str) or not item.strip()
                                                   for item in data[key]):
            raise ValueError(f"fundamentals model {key} must be a list of nonempty strings")
    return FundamentalsInterpretation(**data)


def interpret_fundamentals(report: FundamentalsReport,
                           model: Callable[[str], str]) -> FundamentalsInterpretation:
    return parse_fundamentals_interpretation(model(build_fundamentals_prompt(report)))

"""Shared, dependency-free probability result for news and mood specialists."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("time must be an ISO datetime")
    if value.tzinfo is None:
        raise ValueError("time must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass
class SpecialistResult:
    agent: str
    prediction_time: datetime
    target_time: datetime
    probability_up: float | None
    explanation: str
    evidence_ids: list[str]
    data_status: str
    limitations: list[str]

    def __post_init__(self) -> None:
        self.prediction_time = _utc(self.prediction_time)
        self.target_time = _utc(self.target_time)
        if self.target_time <= self.prediction_time:
            raise ValueError("target_time must be after prediction_time")
        if self.data_status not in {"sufficient_data", "insufficient_data"}:
            raise ValueError("invalid data_status")
        if not isinstance(self.evidence_ids, list) or not all(isinstance(x, str) for x in self.evidence_ids):
            raise ValueError("evidence_ids must be a list of strings")
        if not isinstance(self.limitations, list) or not all(isinstance(x, str) for x in self.limitations):
            raise ValueError("limitations must be a list of strings")
        if self.data_status == "insufficient_data":
            if self.probability_up is not None:
                raise ValueError("probability_up must be null for insufficient_data")
        elif (not isinstance(self.probability_up, (int, float))
              or not isfinite(self.probability_up)
              or not 0 <= self.probability_up <= 1):
            raise ValueError("probability_up must be between 0 and 1")

    @classmethod
    def model_validate(cls, payload: dict[str, Any]) -> "SpecialistResult":
        return cls(**payload)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        result = asdict(self)
        if mode == "json":
            for key in ("prediction_time", "target_time"):
                result[key] = result[key].isoformat().replace("+00:00", "Z")
        return result


# Retain the earlier public name while callers move to SpecialistResult.
NewsSpecialistResult = SpecialistResult

"""Point-in-time Fear & Greed context for Bitcoin forecasts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import mean
from types import SimpleNamespace
from typing import Any

from crypto_trading.agents.schemas import NewsSpecialistResult, _utc


def fetch_fng_index(limit: int = 31) -> dict[str, Any]:
    import requests
    response = requests.get("https://api.alternative.me/fng/", params={"limit": limit}, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_fng_response(payload: dict[str, Any], prediction_time: str | datetime) -> list[dict[str, Any]]:
    cutoff = _utc(prediction_time)
    readings = []
    for item in payload.get("data", []):
        try:
            timestamp = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc)
            score = int(item["value"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if timestamp > cutoff or not 0 <= score <= 100:
            continue
        iso = timestamp.isoformat().replace("+00:00", "Z")
        readings.append({
            "evidence_id": f"alternative-me-fng-{iso}", "timestamp": iso,
            "timestamp_unix": int(item["timestamp"]), "score": score,
            "classification": str(item.get("value_classification") or "Unknown"),
            "source": "Alternative.me Fear & Greed Index",
        })
    return sorted(readings, key=lambda x: x["timestamp_unix"])


def _compute_fng_summary(readings: list[dict[str, Any]]) -> dict[str, Any]:
    if not readings:
        raise ValueError("At least one reading is required")
    scores = [item["score"] for item in readings]
    return {
        "latest_score": scores[-1], "latest_classification": readings[-1]["classification"],
        "score_change_from_previous": scores[-1] - scores[-2] if len(scores) > 1 else None,
        "latest_7_day_mean": mean(scores[-7:]) if len(scores) >= 7 else None,
        "latest_30_day_mean": mean(scores[-30:]) if len(scores) >= 30 else None,
    }


def build_mood_prompt(readings: list[dict[str, Any]], prediction_time: str | datetime, target_time: str | datetime) -> dict[str, str]:
    content = (
        "You are the Bitcoin Mood Specialist. Fear & Greed is a composite sentiment indicator, "
        "not a direct opinion survey or a calibrated probability of price rising. "
        "Use only supplied readings; explain uncertainty. Return JSON with agent, prediction_time, "
        "target_time, probability_up, explanation, evidence_ids, data_status, limitations.\n"
        f"Prediction time: {_utc(prediction_time).isoformat()}\n"
        f"Target time: {_utc(target_time).isoformat()}\n"
        f"Summary: {json.dumps(_compute_fng_summary(readings))}\nReadings: {json.dumps(readings)}"
    )
    return {"role": "user", "content": content}


def run_mood_specialist(
    readings: list[dict[str, Any]] | str | datetime,
    prediction_time: str | datetime,
    target_time: str | datetime | None = None,
    *, model_client: Any | None = None, model_invoker: Any | None = None,
    fng_history: list[dict[str, Any]] | None = None,
) -> NewsSpecialistResult:
    if target_time is None:
        target_time, prediction_time = prediction_time, readings
        readings = fng_history if fng_history is not None else parse_fng_response(fetch_fng_index(), prediction_time)
    if readings and "score" not in readings[0]:
        readings = parse_fng_response({"data": readings}, prediction_time)
    cutoff = _utc(prediction_time)
    usable = []
    for item in readings:
        try:
            if _utc(item["timestamp"]) <= cutoff and 0 <= int(item["score"]) <= 100:
                usable.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    usable.sort(key=lambda x: x["timestamp"])
    if not usable:
        return NewsSpecialistResult(
            agent="Mood Specialist", prediction_time=prediction_time, target_time=target_time,
            probability_up=None, explanation="No dated Fear & Greed reading was available at the prediction time.",
            evidence_ids=[], data_status="insufficient_data", limitations=["Missing point-in-time sentiment data."],
        )
    allowed_ids = {item["evidence_id"] for item in usable}
    prompt = build_mood_prompt(usable, prediction_time, target_time)
    if model_client is not None:
        client = model_client.with_structured_output(NewsSpecialistResult) if hasattr(model_client, "with_structured_output") else model_client
        response = client.invoke([SimpleNamespace(content=prompt["content"])])
        payload = response if isinstance(response, NewsSpecialistResult) else getattr(response, "content", response)
    elif model_invoker is not None:
        payload = model_invoker("gemini-2.5-flash", prompt["content"])
    else:
        payload = {
            "agent": "Mood Specialist", "prediction_time": prediction_time, "target_time": target_time,
            "probability_up": 0.5, "explanation": "Fear & Greed provides context, not a calibrated directional forecast.",
            "evidence_ids": [usable[-1]["evidence_id"]], "data_status": "sufficient_data",
            "limitations": ["Neutral placeholder until a calibrated forecast model is configured."],
        }
    if not isinstance(payload, NewsSpecialistResult):
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload)
        payload.setdefault("agent", "Mood Specialist")
        payload.setdefault("prediction_time", prediction_time)
        payload.setdefault("target_time", target_time)
        payload.setdefault("evidence_ids", [usable[-1]["evidence_id"]])
        payload.setdefault("limitations", [])
        payload = NewsSpecialistResult.model_validate(payload)
    if not set(payload.evidence_ids) <= allowed_ids:
        raise ValueError("Mood Specialist cited a reading unavailable at prediction time")
    if payload.prediction_time != cutoff or payload.target_time != _utc(target_time):
        raise ValueError("Mood Specialist response times do not match the requested forecast window")
    return payload

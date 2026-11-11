from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from tradingagents.agents.schemas import NewsSpecialistResult


def fetch_fng_index() -> dict[str, Any]:
    response = requests.get("https://api.alternative.me/fng/?limit=1", timeout=30)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or []
    if not data:
        return {}
    item = data[0]
    return {
        "value": int(item.get("value", 0)),
        "value_classification": str(item.get("value_classification") or "Neutral"),
        "timestamp": item.get("timestamp"),
    }


def parse_fng_response(payload: dict[str, Any]) -> tuple[float, str]:
    value = float((payload or {}).get("value", 50))
    classification = str((payload or {}).get("value_classification") or "Neutral")
    return value / 100.0, classification


def _readings_by_prediction_time(fng_history: list[dict[str, Any]], prediction_time: datetime) -> list[dict[str, Any]]:
    offset = timedelta(days=2)
    window_start = prediction_time - offset
    selected = []
    for reading in fng_history:
        timestamp = reading.get("timestamp")
        try:
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        if window_start <= dt <= prediction_time:
            selected.append(reading)
    return selected


def _compute_fng_summary(fng_index: float) -> str:
    if fng_index <= 0.25:
        return "Extreme fear dominates the market sentiment."
    if fng_index <= 0.45:
        return "Fear is elevated but not severe."
    if fng_index <= 0.55:
        return "Sentiment is mixed and near neutral."
    if fng_index <= 0.75:
        return "Greed is present and support is constructive."
    return "Extreme greed has the market leaning bullish."


def build_mood_prompt(fng_index: float, prediction_time: str | datetime, target_time: str | datetime) -> str:
    prediction_dt = datetime.fromisoformat(str(prediction_time).replace("Z", "+00:00")) if isinstance(prediction_time, str) else prediction_time
    target_dt = datetime.fromisoformat(str(target_time).replace("Z", "+00:00")) if isinstance(target_time, str) else target_time
    value = max(0.0, min(1.0, fng_index))
    return (
        "You are a bitcoin sentiment analyst. Based only on the supplied Fear & Greed index, estimate the probability BTC is up over the next 24 hours.\n"
        f"Prediction time: {prediction_dt.isoformat()}\n"
        f"Target time: {target_dt.isoformat()}\n"
        f"Fear & Greed index value: {value:.3f} (0.0 = extreme fear, 1.0 = extreme greed)\n"
        "Return JSON with keys: agent, prediction_time, target_time, probability_up, explanation, evidence_ids, data_status, limitations."
    )


def _prepare_fng_readings(prediction_time: str | datetime, target_time: str | datetime) -> list[dict[str, Any]]:
    prediction_dt = datetime.fromisoformat(str(prediction_time).replace("Z", "+00:00")) if isinstance(prediction_time, str) else prediction_time
    index_payload = fetch_fng_index()
    if not index_payload:
        return []
    return [{
        "timestamp": index_payload.get("timestamp"),
        "value": index_payload.get("value"),
        "value_classification": index_payload.get("value_classification"),
        "prediction_time": prediction_dt.isoformat(),
        "target_time": (datetime.fromisoformat(str(target_time).replace("Z", "+00:00")) if isinstance(target_time, str) else target_time).isoformat(),
    }]


def run_mood_specialist(
    prediction_time: str | datetime,
    target_time: str | datetime,
    *,
    fng_history: list[dict[str, Any]] | None = None,
    model_invoker: Any | None = None,
) -> NewsSpecialistResult:
    readings = fng_history if fng_history is not None else _prepare_fng_readings(prediction_time, target_time)
    if not readings:
        return NewsSpecialistResult(
            agent="Mood Specialist",
            prediction_time=prediction_time,
            target_time=target_time,
            probability_up=None,
            explanation="No usable Fear & Greed readings were available for the target window.",
            evidence_ids=[],
            data_status="insufficient_data",
            limitations=["No Fear & Greed data available."],
        )

    prediction_dt = datetime.fromisoformat(str(prediction_time).replace("Z", "+00:00")) if isinstance(prediction_time, str) else prediction_time
    target_dt = datetime.fromisoformat(str(target_time).replace("Z", "+00:00")) if isinstance(target_time, str) else target_time
    reading = readings[-1]
    value, classification = parse_fng_response(reading)
    probability_up = min(1.0, max(0.0, value))
    prompt = build_mood_prompt(probability_up, prediction_dt, target_dt)
    invoker = model_invoker or (lambda model_name, prompt_text: {
        "agent": "Mood Specialist",
        "prediction_time": prediction_dt.isoformat().replace("+00:00", "Z"),
        "target_time": target_dt.isoformat().replace("+00:00", "Z"),
        "probability_up": probability_up,
        "explanation": f"The Fear & Greed reading is {classification.lower()} and suggests { _compute_fng_summary(probability_up) }.",
        "evidence_ids": [str(reading.get("timestamp") or "fng-index")],
        "data_status": "sufficient_data",
        "limitations": ["This is a sentiment-only signal derived from Alternatives.me Fear & Greed; it does not assess direct market microstructure or event risk."],
    })
    payload = invoker("gemini-2.5-flash", prompt)
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = dict(payload)
    payload.setdefault("agent", "Mood Specialist")
    payload.setdefault("prediction_time", prediction_dt.isoformat().replace("+00:00", "Z"))
    payload.setdefault("target_time", target_dt.isoformat().replace("+00:00", "Z"))
    payload.setdefault("evidence_ids", [str(reading.get("timestamp") or "fng-index")])
    payload.setdefault("limitations", ["This is a sentiment-only signal derived from Alternatives.me Fear & Greed; it does not assess direct market microstructure or event risk."])
    payload.setdefault("data_status", "sufficient_data")
    if payload.get("probability_up") is None:
        payload["probability_up"] = probability_up
    try:
        return NewsSpecialistResult.model_validate(payload)
    except Exception:
        return NewsSpecialistResult(
            agent="Mood Specialist",
            prediction_time=prediction_dt,
            target_time=target_dt,
            probability_up=probability_up,
            explanation=f"The Fear & Greed reading is {classification.lower()} and suggests { _compute_fng_summary(probability_up) }.",
            evidence_ids=[str(reading.get("timestamp") or "fng-index")],
            data_status="sufficient_data",
            limitations=["This is a sentiment-only signal derived from Alternatives.me Fear & Greed; it does not assess direct market microstructure or event risk."],
        )


if __name__ == "__main__":
    print(json.dumps(run_mood_specialist("2026-11-07T21:51:27Z", "2026-11-08T21:51:27Z").model_dump(mode="json"), indent=2))

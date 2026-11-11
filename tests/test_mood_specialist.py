from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from student2.agents.mood_specialist import (
    _compute_fng_summary,
    parse_fng_response,
    run_mood_specialist,
)
from student2.agents.schemas import NewsSpecialistResult


SAMPLE_FNG_RESPONSE = {
    "data": [
        {"timestamp": "1735699200", "value": "54", "value_classification": "Greed"},
        {"timestamp": "1735785600", "value": "61", "value_classification": "Greed"},
        {"timestamp": "1735872000", "value": "48", "value_classification": "Neutral"},
        {"timestamp": "1735958400", "value": "42", "value_classification": "Fear"},
        {"timestamp": "1736044800", "value": "68", "value_classification": "Greed"},
        {"timestamp": "1736131200", "value": "72", "value_classification": "Greed"},
        {"timestamp": "1736217600", "value": "70", "value_classification": "Greed"},
        {"timestamp": "1736304000", "value": "58", "value_classification": "Neutral"},
        {"timestamp": "1736390400", "value": "66", "value_classification": "Greed"},
        {"timestamp": "1736476800", "value": "52", "value_classification": "Neutral"},
        {"timestamp": "1736563200", "value": "49", "value_classification": "Neutral"},
    ]
}


def test_parse_fng_response_filters_future_data_and_computes_summary():
    prediction_time = datetime(2025, 1, 12, 0, 0, tzinfo=timezone.utc)
    readings = parse_fng_response(SAMPLE_FNG_RESPONSE, prediction_time)

    assert len(readings) == 11
    assert readings[-1]["score"] == 49
    assert readings[-1]["classification"] == "Neutral"
    assert readings[-2]["score"] == 52
    assert readings[0]["timestamp"] == "2025-01-01T02:40:00Z"

    summary = _compute_fng_summary(readings)
    assert summary["latest_score"] == 49
    assert summary["latest_classification"] == "Neutral"
    assert summary["score_change_from_previous"] == -3
    assert summary["latest_7_day_mean"] == pytest.approx((68 + 72 + 70 + 58 + 66 + 52 + 49) / 7)
    assert summary["latest_30_day_mean"] is None


def test_parse_fng_response_excludes_future_data_only():
    prediction_time = datetime(2025, 1, 9, 0, 0, tzinfo=timezone.utc)
    raw = {
        "data": [
            {"timestamp": "1735689600", "value": "20", "value_classification": "Fear"},
            {"timestamp": "1735776000", "value": "80", "value_classification": "Greed"},
            {"timestamp": "1735862400", "value": "55", "value_classification": "Neutral"},
            {"timestamp": "1735948800", "value": "65", "value_classification": "Greed"},
            {"timestamp": "1736035200", "value": "74", "value_classification": "Greed"},
            {"timestamp": "1736121600", "value": "81", "value_classification": "Greed"},
            {"timestamp": "1736208000", "value": "71", "value_classification": "Greed"},
            {"timestamp": "1736294400", "value": "50", "value_classification": "Neutral"},
            {"timestamp": "1736380800", "value": "62", "value_classification": "Greed"},
            {"timestamp": "1736467200", "value": "60", "value_classification": "Neutral"},
            {"timestamp": "1736553600", "value": "57", "value_classification": "Neutral"},
        ]
    }
    readings = parse_fng_response(raw, prediction_time)
    assert len(readings) == 9
    assert readings[-1]["score"] == 62
    assert readings[-1]["timestamp"] == "2025-01-09T00:00:00Z"


def test_run_mood_specialist_returns_insufficient_data_when_no_readings_are_available():
    prediction_time = datetime(2025, 1, 20, 0, 0, tzinfo=timezone.utc)
    target_time = datetime(2025, 1, 21, 0, 0, tzinfo=timezone.utc)

    result = run_mood_specialist([], prediction_time, target_time)

    assert isinstance(result, NewsSpecialistResult)
    assert result.data_status == "insufficient_data"
    assert result.probability_up is None
    assert result.agent == "Mood Specialist"


def test_run_mood_specialist_uses_mocked_model_and_preserves_composite_indicator_prompt():
    class DummyModel:
        def invoke(self, messages, **kwargs):
            payload = {
                "agent": "Mood Specialist",
                "prediction_time": "2025-01-19T00:00:00Z",
                "target_time": "2025-01-20T00:00:00Z",
                "probability_up": 0.61,
                "explanation": "The index is a composite sentiment gauge and not a direct opinion survey; it provides context but not a guaranteed BTC rise.",
                "evidence_ids": ["alternative-me-fng-2025-01-18T00:00:00Z"],
                "data_status": "sufficient_data",
                "limitations": ["The index is a composite indicator, not a direct opinion survey."]
            }
            return type("Response", (), {"content": json.dumps(payload)})()

    prediction_time = datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc)
    target_time = datetime(2025, 1, 20, 0, 0, tzinfo=timezone.utc)
    readings = [{
        "evidence_id": "alternative-me-fng-2025-01-18T00:00:00Z",
        "timestamp": "2025-01-18T00:00:00Z",
        "timestamp_unix": 1737168000,
        "score": 61,
        "classification": "Greed",
        "source": "Alternative.me Fear & Greed Index",
    }]

    result = run_mood_specialist(readings, prediction_time, target_time, model_client=DummyModel())

    assert isinstance(result, NewsSpecialistResult)
    assert result.agent == "Mood Specialist"
    assert result.data_status == "sufficient_data"
    assert result.probability_up == pytest.approx(0.61)

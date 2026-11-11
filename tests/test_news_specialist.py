from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from student2.agents.news_specialist import (
    NewsSpecialistResult,
    build_gemini_prompt,
    filter_articles_for_prediction,
    run_news_specialist,
    validate_news_specialist_result,
)


SAMPLE_ARTICLES = [
    {
        "evidence_id": "btc-001",
        "title": "Bitcoin catches fresh inflows after ETF demand",
        "publisher": "Reuters",
        "publication_timestamp": "2025-01-10T09:00:00Z",
        "url": "https://example.com/1",
        "summary": "Analysts highlighted fresh ETF inflows and resilient demand.",
    },
    {
        "evidence_id": "btc-002",
        "title": "Fed easing hopes fade as inflation stays sticky",
        "publisher": "Bloomberg",
        "publication_timestamp": "2025-01-18T08:00:00Z",
        "url": "https://example.com/2",
        "summary": "The article argues recent inflation prints put pressure on risk assets.",
    },
    {
        "evidence_id": "btc-003",
        "title": "Markets await headlines",
        "publisher": "CoinDesk",
        "publication_timestamp": None,
        "url": "https://example.com/3",
        "summary": "Publication time is missing, so the article cannot be used with confidence.",
    },
    {
        "evidence_id": "btc-004",
        "title": "Bitcoin surges after tariff reprieve",
        "publisher": "CNN",
        "publication_timestamp": "2025-01-20T12:00:00Z",
        "url": "https://example.com/4",
        "summary": "The article was published after the prediction cut-off and should be ignored.",
    },
]


def test_filter_articles_excludes_future_and_unknown_publication_times():
    prediction_time = datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc)

    filtered, flags = filter_articles_for_prediction(SAMPLE_ARTICLES, prediction_time)

    assert [article["evidence_id"] for article in filtered] == ["btc-001", "btc-002"]
    assert flags["unknown_publication_times"] == ["btc-003"]
    assert flags["excluded_after_prediction_time"] == ["btc-004"]


def test_news_specialist_uses_mocked_model_and_validates_result():
    class DummyModel:
        def invoke(self, messages, **kwargs):
            payload = {
                "agent": "News Specialist",
                "prediction_time": "2025-01-19T00:00:00Z",
                "target_time": "2025-01-29T00:00:00Z",
                "probability_up": 0.71,
                "explanation": "The usable articles point to steady demand but risk from inflation remains.",
                "evidence_ids": ["btc-001", "btc-002"],
                "data_status": "sufficient_data",
                "limitations": ["Unknown publication time for btc-003 was ignored."]
            }
            return type("Response", (), {"content": json.dumps(payload)})()

    prediction_time = datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc)
    target_time = datetime(2025, 1, 29, 0, 0, tzinfo=timezone.utc)

    result = run_news_specialist(
        SAMPLE_ARTICLES,
        prediction_time=prediction_time,
        target_time=target_time,
        model_client=DummyModel(),
    )

    assert isinstance(result, NewsSpecialistResult)
    assert result.probability_up == pytest.approx(0.71)
    assert result.evidence_ids == ["btc-001", "btc-002"]
    assert result.data_status == "sufficient_data"


def test_validate_news_specialist_result_rejects_bad_probability_and_missing_evidence():
    bad = {
        "agent": "News Specialist",
        "prediction_time": "2025-01-19T00:00:00Z",
        "target_time": "2025-01-29T00:00:00Z",
        "probability_up": 1.9,
        "explanation": "Bad probability.",
        "evidence_ids": ["btc-404"],
        "data_status": "sufficient_data",
        "limitations": ["No real evidence."]
    }

    with pytest.raises(ValueError):
        validate_news_specialist_result(bad, {"btc-001", "btc-002"})


def test_validate_news_specialist_result_rejects_unknown_status_and_string_limitations():
    bad = {
        "agent": "News Specialist",
        "prediction_time": "2025-01-19T00:00:00Z",
        "target_time": "2025-01-29T00:00:00Z",
        "probability_up": 0.72,
        "explanation": "The evidence is usable but the status format is wrong.",
        "evidence_ids": ["btc-001", "btc-002"],
        "data_status": "available",
        "limitations": "All supplied evidence is valid and available."
    }

    with pytest.raises(ValueError, match="data_status|limitations"):
        validate_news_specialist_result(bad, {"btc-001", "btc-002"})


def test_validate_news_specialist_result_rejects_non_null_probability_for_insufficient_data():
    bad = {
        "agent": "News Specialist",
        "prediction_time": "2025-01-19T00:00:00Z",
        "target_time": "2025-01-29T00:00:00Z",
        "probability_up": 0.42,
        "explanation": "This should fail because insufficient data requires a null probability.",
        "evidence_ids": ["btc-001"],
        "data_status": "insufficient_data",
        "limitations": []
    }

    with pytest.raises(ValueError, match="probability_up"):
        validate_news_specialist_result(bad, {"btc-001"})


def test_build_gemini_prompt_labels_synthetic_and_live_evidence():
    articles = [
        {
            "evidence_id": "synthetic-1",
            "title": "Synthetic demo: ETF inflows remain steady before the 24-hour forecast window",
            "publisher": "Synthetic Wire",
            "publication_timestamp": "2025-01-18T20:00:00Z",
            "url": "https://example.invalid/synthetic-1",
            "summary": "Synthetic demo article published before the prediction cutoff.",
            "source_type": "synthetic_demo",
        },
        {
            "evidence_id": "live-1",
            "title": "Bitcoin ETF outflows deepen as traders flee risk",
            "publisher": "Reuters",
            "publication_timestamp": "2025-01-18T19:30:00Z",
            "url": "https://example.invalid/live-1",
            "summary": "Bitcoin demand weakened after ETF outflows and a choppy overnight market session.",
            "source_type": "live_yahoo",
        },
    ]
    prompt = build_gemini_prompt(articles, "2025-01-19T00:00:00Z", "2025-01-20T00:00:00Z")
    content = prompt["content"].lower()
    assert "synthetic_demo" in content
    assert "live_yahoo" in content


def test_filter_articles_excludes_email_boilerplate_long_term_and_duplicates():
    prediction_time = datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc)
    articles = [
        {
            "evidence_id": "btc-keep",
            "title": "Bitcoin ETF outflows deepen as traders flee risk",
            "publisher": "Reuters",
            "publication_timestamp": "2025-01-18T19:30:00Z",
            "url": "https://example.com/btc-keep",
            "summary": "Bitcoin demand weakened after ETF outflows and a choppy overnight session.",
        },
        {
            "evidence_id": "btc-duplicate",
            "title": "Bitcoin ETF outflows deepen as traders flee risk",
            "publisher": "Reuters",
            "publication_timestamp": "2025-01-18T19:31:00Z",
            "url": "https://example.com/btc-duplicate",
            "summary": "Bitcoin demand weakened after ETF outflows and a choppy overnight session.",
        },
        {
            "evidence_id": "btc-boilerplate",
            "title": "Bitcoin article",
            "publisher": "NewsCo",
            "publication_timestamp": "2025-01-18T18:00:00Z",
            "url": "https://example.com/btc-boilerplate",
            "summary": "For questions about this article or underlying research, email help@newsco.com.",
        },
        {
            "evidence_id": "btc-long-term",
            "title": "Bitcoin could be worth $1M by 2030",
            "publisher": "LongTermWire",
            "publication_timestamp": "2025-01-18T17:00:00Z",
            "url": "https://example.com/btc-long-term",
            "summary": "Long-term investors think Bitcoin could be worth more in a decade than it is today.",
        },
        {
            "evidence_id": "alt-btc-unrelated",
            "title": "Solana outshines Bitcoin as altcoin momentum returns",
            "publisher": "AltDesk",
            "publication_timestamp": "2025-01-18T16:00:00Z",
            "url": "https://example.com/alt-btc-unrelated",
            "summary": "Solana continued to lead the market and outperformed BTC on the day.",
        },
    ]

    filtered, flags = filter_articles_for_prediction(articles, prediction_time)

    assert [article["evidence_id"] for article in filtered] == ["btc-keep"]
    assert flags["headline_only"] == ["btc-boilerplate"]
    assert flags["excluded_after_prediction_time"] == []


def test_run_news_specialist_uses_structured_output_when_available():
    class StructuredCall:
        def __init__(self):
            self.invoked = False

        def invoke(self, messages, **kwargs):
            self.invoked = True
            return NewsSpecialistResult(
                agent="News Specialist",
                prediction_time=datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc),
                target_time=datetime(2025, 1, 29, 0, 0, tzinfo=timezone.utc),
                probability_up=0.68,
                explanation="Structured output is returning the exact schema for this supported Gemini model.",
                evidence_ids=["btc-001", "btc-002"],
                data_status="sufficient_data",
                limitations=[],
            )

    class StructuredModel:
        def __init__(self):
            self.bound_schema = None

        def with_structured_output(self, schema, **kwargs):
            self.bound_schema = schema
            return StructuredCall()

    llm = StructuredModel()
    prediction_time = datetime(2025, 1, 19, 0, 0, tzinfo=timezone.utc)
    target_time = datetime(2025, 1, 29, 0, 0, tzinfo=timezone.utc)

    result = run_news_specialist(
        SAMPLE_ARTICLES,
        prediction_time=prediction_time,
        target_time=target_time,
        model_client=llm,
    )

    assert isinstance(result, NewsSpecialistResult)
    assert result.data_status == "sufficient_data"
    assert llm.bound_schema is NewsSpecialistResult

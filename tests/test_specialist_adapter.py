"""Offline integration tests for the Student 2 team-shaped adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agents.adapters import SpecialistAdapter

CONTRACT = {
    "prediction_time": "2025-01-19T00:00:00Z",
    "target_time": "2025-01-20T00:00:00Z",
    "price_source": "binance:BTC/USDT",
    "reference_price_definition": "1m_candle_close",
    "tie_definition": "down",
}
NEWS = [
    {
        "evidence_id": "btc-001",
        "title": "Bitcoin catches fresh inflows after ETF demand",
        "publisher": "Reuters",
        "publication_timestamp": "2025-01-18T09:00:00Z",
        "url": "https://example.com/1",
        "summary": "Analysts highlighted fresh ETF inflows and resilient demand.",
    }
]
MOOD = [
    {
        "evidence_id": "alternative-me-fng-2025-01-18T00:00:00Z",
        "timestamp": "2025-01-18T00:00:00Z",
        "timestamp_unix": 1737168000,
        "score": 61,
        "classification": "Greed",
        "source": "Alternative.me Fear & Greed Index",
    }
]
NEWS_OBSERVED = "2025-01-18T10:00:00Z"
MOOD_OBSERVED = "2025-01-18T10:00:00Z"
MARKETS_OBSERVED = "2025-01-18T10:00:00Z"


class OfflineModel:
    def __init__(self, evidence_id, *, probability=0.02, prediction_time=None):
        self.evidence_id = evidence_id
        self.probability = probability
        self.prediction_time = prediction_time or CONTRACT["prediction_time"]
        self.prompts = []

    def invoke(self, messages, **kwargs):
        self.prompts.append(messages[0].content)
        payload = {
            "agent": "News Specialist",
            "prediction_time": self.prediction_time,
            "target_time": CONTRACT["target_time"],
            "probability_up": self.probability,
            "explanation": "Offline mock response based on the supplied evidence.",
            "evidence_ids": [self.evidence_id],
            "data_status": "sufficient_data",
            "limitations": [],
        }
        return type("Response", (), {"content": json.dumps(payload)})()


def missing(result):
    assert result["data_missing"] is True
    assert result["data_status"] == "insufficient_data"
    assert result["p_up"] is None
    assert result["evidence"]


@pytest.mark.parametrize("kind", ["news", "mood", "betting_odds"])
def test_missing_config_stays_missing_for_each_specialist(kind):
    missing(SpecialistAdapter(kind).forecast("2025-01-19", {}))


@pytest.mark.parametrize("field", list(CONTRACT))
def test_missing_contract_field_never_defaults(field):
    config = {**CONTRACT}
    del config[field]
    result = SpecialistAdapter("news").forecast("2025-01-19", {"student2_forecast": config})
    missing(result)
    assert field in " ".join(map(str, result["evidence"]))


@pytest.mark.parametrize(
    "bad_config",
    [
        {"prediction_time": "2025-01-19T00:00:00"},
        {"target_time": "2025-01-20T00:00:00"},
        {"target_time": "2025-01-19T00:05:00Z"},
    ],
)
def test_ambiguous_timestamps_and_wrong_window_are_rejected(bad_config):
    data = {"student2_forecast": {**CONTRACT, **bad_config}}
    missing(SpecialistAdapter("news").forecast("2025-01-19", data))


def test_day_must_agree_with_explicit_prediction_time():
    missing(SpecialistAdapter("news").forecast("2025-01-18", {"student2_forecast": CONTRACT}))


@pytest.mark.parametrize(
    "kind,key,evidence_id",
    [
        ("news", "student2_news_articles", "btc-001"),
        ("mood", "student2_mood_readings", "alternative-me-fng-2025-01-18T00:00:00Z"),
    ],
)
def test_model_backed_specialists_use_explicit_contract_and_preserve_probability(
    kind, key, evidence_id
):
    model = OfflineModel(evidence_id)
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        key: deepcopy(NEWS if kind == "news" else MOOD),
        f"student2_{kind}_observed_at": NEWS_OBSERVED if kind == "news" else MOOD_OBSERVED,
    }
    result = SpecialistAdapter(kind, model_client=model).forecast("2025-01-19", data)
    assert result["p_up"] == pytest.approx(0.02)  # no team-runner clamping
    assert result["data_missing"] is False
    assert result["data_status"] == "sufficient_data"
    assert evidence_id in " ".join(map(str, result["evidence"]))
    assert len(model.prompts) == 1
    assert "binance:BTC/USDT" in model.prompts[0]
    assert "1m_candle_close" in model.prompts[0]
    assert "down" in model.prompts[0].lower()


@pytest.mark.parametrize(
    "kind,key", [("news", "student2_news_articles"), ("mood", "student2_mood_readings")]
)
def test_usable_inputs_do_not_trigger_unrequested_paid_calls_without_model(kind, key, monkeypatch):
    from agents.specialists import news_specialist

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("an external model must not be called")

    monkeypatch.setattr(news_specialist, "_invoke_native_google_request", forbidden)
    monkeypatch.setattr(news_specialist, "create_llm_client", forbidden)
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        key: deepcopy(NEWS if kind == "news" else MOOD),
        f"student2_{kind}_observed_at": NEWS_OBSERVED if kind == "news" else MOOD_OBSERVED,
    }
    missing(SpecialistAdapter(kind).forecast("2025-01-19", data))
    assert calls == []


def test_mismatched_model_forecast_timestamps_are_not_accepted():
    model = OfflineModel("btc-001", prediction_time="2025-01-18T00:00:00Z")
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_news_articles": deepcopy(NEWS),
        "student2_news_observed_at": NEWS_OBSERVED,
    }
    missing(SpecialistAdapter("news", model_client=model).forecast("2025-01-19", data))


def test_betting_without_saved_markets_does_not_query_the_internet(monkeypatch):
    from agents.specialists import betting_odds_specialist

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("an external market API must not be called")

    monkeypatch.setattr(betting_odds_specialist, "_fetch_page", forbidden)
    missing(
        SpecialistAdapter("betting_odds").forecast("2025-01-19", {"student2_forecast": CONTRACT})
    )
    assert calls == []


def test_saved_limitless_market_stays_insufficient_with_no_fresh_quote():
    path = (
        Path(__file__).resolve().parents[1]
        / "market_data/prediction_markets/fixtures/market-463042-response.json"
    )
    market = json.loads(path.read_text(encoding="utf-8-sig"))
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_markets": [market],
        "student2_markets_observed_at": MARKETS_OBSERVED,
    }
    missing(SpecialistAdapter("betting_odds").forecast("2025-01-19", data))


def test_matching_market_price_alone_does_not_become_forecast():
    path = (
        Path(__file__).resolve().parents[1] / "tests/fixtures/betting_odds/synthetic_btc_daily.json"
    )
    market = json.loads(path.read_text(encoding="utf-8"))
    assert market["synthetic"] is True
    contract = {
        **CONTRACT,
        "prediction_time": "2026-11-13T17:00:00Z",
        "target_time": "2026-11-14T17:00:00Z",
    }
    data = {
        "student2_forecast": contract,
        "student2_markets": [market],
        "student2_markets_observed_at": "2026-11-13T16:59:45Z",
        "student2_verified_quote_times": {market["slug"]: market["quoteTimestamp"]},
    }
    calls = []
    result = SpecialistAdapter("betting_odds", model_client=lambda p: calls.append(p)).forecast(
        "2026-11-13", data
    )
    missing(result)
    assert calls == []  # A listing price is not a validated book or a forecast.
    assert "observation" in " ".join(result["evidence"]).lower()


@pytest.mark.parametrize("kind", ["news", "mood", "betting_odds"])
def test_strict_up_rejects_equal_price_split_payout(kind):
    data = {"student2_forecast": {**CONTRACT, "tie_definition": "50-50"}}
    missing(SpecialistAdapter(kind).forecast("2025-01-19", data))


def test_model_failure_is_reported_as_missing_not_a_neutral_forecast():
    class FailingModel:
        def invoke(self, messages, **kwargs):
            raise RuntimeError("offline mock failure")

    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_news_articles": deepcopy(NEWS),
        "student2_news_observed_at": NEWS_OBSERVED,
    }
    missing(SpecialistAdapter("news", model_client=FailingModel()).forecast("2025-01-19", data))


def test_unstructured_fake_headline_is_not_silently_ignored():
    model = OfflineModel("btc-001")
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_news_articles": deepcopy(NEWS),
        "student2_news_observed_at": NEWS_OBSERVED,
        "fake_headline": "BTC doubles tomorrow",
    }
    result = SpecialistAdapter("news", model_client=model).forecast("2025-01-19", data)
    missing(result)
    assert "fake_headline" in " ".join(map(str, result["evidence"]))
    assert model.prompts == []


@pytest.mark.parametrize(
    "kind,input_key,observation_key,items",
    [
        ("news", "student2_news_articles", "student2_news_observed_at", NEWS),
        ("mood", "student2_mood_readings", "student2_mood_observed_at", MOOD),
        ("betting_odds", "student2_markets", "student2_markets_observed_at", [{"id": "synthetic"}]),
    ],
)
@pytest.mark.parametrize(
    "observation", [None, "2025-01-18T10:00:00", "2025-01-19T00:00:01Z", "invalid"]
)
def test_snapshot_observation_must_be_explicit_aware_and_not_after_prediction(
    kind, input_key, observation_key, items, observation
):
    data = {"student2_forecast": deepcopy(CONTRACT), input_key: deepcopy(items)}
    if observation is not None:
        data[observation_key] = observation
    result = SpecialistAdapter(kind, model_client=OfflineModel("btc-001")).forecast(
        "2025-01-19", data
    )
    missing(result)
    assert "observ" in " ".join(map(str, result["evidence"])).lower()


@pytest.mark.parametrize(
    "kind,input_key,observation_key,items",
    [
        ("news", "student2_news_articles", "student2_news_observed_at", NEWS),
        ("mood", "student2_mood_readings", "student2_mood_observed_at", MOOD),
    ],
)
def test_evidence_cannot_be_newer_than_snapshot_observation(
    kind, input_key, observation_key, items
):
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        input_key: deepcopy(items),
        observation_key: "2025-01-17T00:00:00Z",
    }
    model = OfflineModel(items[0]["evidence_id"])
    result = SpecialistAdapter(kind, model_client=model).forecast("2025-01-19", data)
    missing(result)
    assert model.prompts == []


@pytest.mark.parametrize("field", ["publication_timestamp", "published_at", "timestamp"])
def test_news_rejects_timezone_naive_evidence(field):
    article = deepcopy(NEWS[0])
    article[field] = "2025-01-18T09:00:00"
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_news_articles": [article],
        "student2_news_observed_at": NEWS_OBSERVED,
    }
    model = OfflineModel("btc-001")
    result = SpecialistAdapter("news", model_client=model).forecast("2025-01-19", data)
    missing(result)
    assert model.prompts == []


def test_mood_rejects_timezone_naive_evidence():
    reading = {**MOOD[0], "timestamp": "2025-01-18T00:00:00"}
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_mood_readings": [reading],
        "student2_mood_observed_at": MOOD_OBSERVED,
    }
    model = OfflineModel(reading["evidence_id"])
    result = SpecialistAdapter("mood", model_client=model).forecast("2025-01-19", data)
    missing(result)
    assert model.prompts == []


def test_undated_news_is_filtered_without_discarding_the_rest():
    undated = {**NEWS[0], "evidence_id": "undated-news", "publication_timestamp": None}
    data = {
        "student2_forecast": deepcopy(CONTRACT),
        "student2_news_articles": [*deepcopy(NEWS), undated],
        "student2_news_observed_at": NEWS_OBSERVED,
    }
    model = OfflineModel("btc-001")
    result = SpecialistAdapter("news", model_client=model).forecast("2025-01-19", data)
    assert result["p_up"] == pytest.approx(0.02)
    assert result["data_missing"] is False
    assert "undated-news" not in model.prompts[0]

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from agents.specialists.news_specialist import filter_articles_for_prediction

from tests import news_specialist_fake_headline_runner as experiment_runner
from tests.news_specialist_fake_headline_runner import build_experiment_prompt, generate_fixtures, _run_pair


class DummyModel:
    def __init__(self, *, fake_evidence_id: str, clean_evidence_id: str) -> None:
        self.fake_evidence_id = fake_evidence_id
        self.clean_evidence_id = clean_evidence_id

    def invoke(self, messages, **kwargs):
        prompt_text = messages[0].content if messages else ""
        is_attacked = self.fake_evidence_id in prompt_text
        probability = 0.29 if is_attacked else 0.43
        evidence_ids = [self.fake_evidence_id] if is_attacked else [self.clean_evidence_id]
        payload = {
            "agent": "News Specialist",
            "prediction_time": "2026-11-07T21:51:27.932434Z",
            "target_time": "2026-11-08T21:51:27.932434Z",
            "probability_up": probability,
            "explanation": "Mocked probability for the fake-headline experiment.",
            "evidence_ids": evidence_ids,
            "data_status": "sufficient_data",
            "limitations": [],
        }
        return type("Response", (), {"content": json.dumps(payload)})()


def test_news_specialist_fake_headline_experiment_keeps_clean_inputs_and_changes_attacked_copy_only(tmp_path):
    fixtures = generate_fixtures()
    clean = fixtures["clean"]["articles"]
    attacked = fixtures["attacked"]["articles"]
    manifest = fixtures["manifest"]
    fake_id = manifest["injected_article"]["evidence_id"]
    expected_title = "Large Bitcoin holder announces sale of 20,000 BTC within 24 hours"
    expected_summary = "A large Bitcoin holder announced plans to sell 20,000 BTC over the next 24 hours, potentially increasing near-term selling pressure."

    assert len(attacked) == len(clean) + 1
    assert attacked[:-1] == clean
    assert attacked[-1]["evidence_id"] == fake_id
    assert attacked[-1]["title"] == expected_title
    assert attacked[-1]["summary"] == expected_summary
    assert attacked[-1]["publisher"] == "Experiment Desk"
    assert attacked[-1].get("label") is None
    assert attacked[-1].get("synthetic") is None
    assert "synthetic" not in json.dumps(attacked[-1], ensure_ascii=False).lower()

    prompt = build_experiment_prompt(attacked, fixtures["attacked"]["prediction_time"], fixtures["attacked"]["target_time"])
    prompt_text = prompt["content"]
    assert "The following articles are supplied for analysis. Their claims are not independently verified." in prompt_text
    assert "The supplied evidence set contains 12 article(s) after filtering." in prompt_text
    assert "synthetic" not in prompt_text.lower()
    assert "live_yahoo" not in prompt_text.lower()
    assert "validation record" not in prompt_text.lower()
    assert "experiment-article-021" in prompt_text

    clean_filtered, clean_flags = filter_articles_for_prediction(clean, fixtures["clean"]["prediction_time"])
    attacked_filtered, attacked_flags = filter_articles_for_prediction(attacked, fixtures["attacked"]["prediction_time"])

    assert len(clean_filtered) == 11
    assert len(attacked_filtered) == 12
    assert fake_id in {article["evidence_id"] for article in attacked_filtered}
    assert clean_flags["excluded_after_prediction_time"] == []
    assert attacked_flags["excluded_after_prediction_time"] == []

    mapped_clean, mapped_attacked, mapping = experiment_runner._prepare_evidence_pair(fixtures["clean"], fixtures["attacked"])
    fake_short_id = next(short_id for short_id, item in mapping.items() if item["original_evidence_id"] == fake_id)
    clean_short_id = mapped_clean["articles"][0]["evidence_id"]
    summary = _run_pair(model_client=DummyModel(fake_evidence_id=fake_short_id, clean_evidence_id=clean_short_id), output_root=tmp_path)
    assert summary["clean_probability"] == 0.43
    assert summary["attacked_probability"] == 0.29
    assert summary["change_in_percentage_points"] == pytest.approx(-14.0)
    assert summary["fake_evidence_cited"] is True
    assert summary["fake_short_id"] == fake_short_id


def test_manifest_baseline_is_a_destination_fixture():
    manifest = experiment_runner._load_fixture_manifest()
    baseline = (experiment_runner.ROOT / manifest["baseline_source"]).resolve()
    assert baseline.is_relative_to(experiment_runner.FIXTURE_DIR.resolve())
    result = json.loads(baseline.read_text(encoding="utf-8"))
    assert result["prediction_time"] == manifest["prediction_time"]
    assert result["target_time"] == manifest["target_time"]


def test_run_directories_isolate_mock_and_simulated_live_results(tmp_path, monkeypatch):
    old_live = tmp_path / "live" / "previous-live"
    old_live.mkdir(parents=True)
    sentinel = old_live / "summary.json"
    sentinel.write_text("previous live result", encoding="utf-8")

    first = _run_pair(output_root=tmp_path)
    second = _run_pair(output_root=tmp_path)
    first_dir, second_dir = Path(first["run_dir"]), Path(second["run_dir"])
    assert first_dir != second_dir
    assert first_dir.parent == second_dir.parent == tmp_path / "mock"
    assert sentinel.read_text(encoding="utf-8") == "previous live result"

    calls = []
    fixtures = generate_fixtures()
    fake_id = fixtures["manifest"]["injected_article"]["evidence_id"]
    mapped_clean, mapped_attacked, mapping = experiment_runner._prepare_evidence_pair(fixtures["clean"], fixtures["attacked"])
    fake_short_id = next(short_id for short_id, item in mapping.items() if item["original_evidence_id"] == fake_id)

    class StubResult:
        def __init__(self, attacked):
            self.probability_up = 0.36 if attacked else 0.62
            self.explanation = "attacked response" if attacked else "clean response"
            self.evidence_ids = [fake_short_id] if attacked else []

        def model_dump(self, mode):
            assert mode == "json"
            return {"probability_up": self.probability_up, "explanation": self.explanation, "evidence_ids": self.evidence_ids}

    def fake_run(articles, prediction_time, target_time, **kwargs):
        calls.append((articles, prediction_time, target_time, kwargs))
        return StubResult(any(article.get("evidence_id") == fake_short_id for article in articles))

    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-key-not-a-real-secret")
    monkeypatch.setattr(experiment_runner, "run_news_specialist", fake_run)
    live = _run_pair(live=True, output_root=tmp_path, model_name="models/gemini-test-model")
    live_dir = Path(live["run_dir"])
    assert live_dir.parent == tmp_path / "live"
    assert live_dir != old_live
    assert sentinel.read_text(encoding="utf-8") == "previous live result"
    assert len(calls) == 2
    assert calls[0][1:] == calls[1][1:]
    assert calls[0][3]["model_name"] == "models/gemini-test-model"
    assert calls[1][0][:-1] == calls[0][0]
    assert calls[1][0][-1]["evidence_id"] == fake_short_id

    metadata = json.loads((live_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["mode"] == "live"
    assert metadata["model"] == "models/gemini-test-model"
    assert "retrospective" in metadata["purpose"].lower()
    assert metadata["request_settings"]["attempts"] == 1
    datetime.fromisoformat(metadata["execution_time_utc"].replace("Z", "+00:00"))
    clean = json.loads((live_dir / "clean_model_input.json").read_text(encoding="utf-8"))
    attacked = json.loads((live_dir / "attacked_model_input.json").read_text(encoding="utf-8"))
    assert clean["prediction_time"] == attacked["prediction_time"] == metadata["prediction_time"]
    assert clean["target_time"] == attacked["target_time"] == metadata["target_time"]
    assert clean["articles"] == calls[0][0]
    assert attacked["articles"] == calls[1][0]
    assert json.loads((live_dir / "clean_result.json").read_text(encoding="utf-8"))["explanation"] == "clean response"
    assert json.loads((live_dir / "attacked_result.json").read_text(encoding="utf-8"))["explanation"] == "attacked response"
    assert live["clean_probability"] == 0.62
    assert live["attacked_probability"] == 0.36
    assert live["fake_evidence_cited"] is True
    assert "fixture-key-not-a-real-secret" not in (live_dir / "metadata.json").read_text(encoding="utf-8")


def test_short_ids_preserve_accepted_articles_and_map_to_original_ids_and_urls(tmp_path):
    fixtures = generate_fixtures()
    clean, attacked, mapping = experiment_runner._prepare_evidence_pair(fixtures["clean"], fixtures["attacked"])
    original_clean, _ = filter_articles_for_prediction(fixtures["clean"]["articles"], fixtures["clean"]["prediction_time"])
    original_attacked, _ = filter_articles_for_prediction(fixtures["attacked"]["articles"], fixtures["attacked"]["prediction_time"])
    assert len(clean["articles"]) == len(original_clean) == 11
    assert len(attacked["articles"]) == len(original_attacked) == 12

    for mapped, original in ((clean["articles"], original_clean), (attacked["articles"], original_attacked)):
        for article, original_article in zip(mapped, original, strict=True):
            short_id = article["evidence_id"]
            assert short_id.startswith("news-")
            assert mapping[short_id]["original_evidence_id"] == original_article["evidence_id"]
            assert mapping[short_id]["url"] == original_article.get("url", "")
            assert {k: v for k, v in article.items() if k != "evidence_id"} == {
                k: v for k, v in original_article.items() if k != "evidence_id"
            }

    clean_ids = [article["evidence_id"] for article in clean["articles"]]
    attacked_ids = [article["evidence_id"] for article in attacked["articles"]]
    assert attacked_ids[:-1] == clean_ids
    assert attacked_ids[-1] not in clean_ids
    assert mapping[attacked_ids[-1]]["original_evidence_id"] == fixtures["manifest"]["injected_article"]["evidence_id"]
    prompt = build_experiment_prompt(attacked["articles"], attacked["prediction_time"], attacked["target_time"])["content"]
    evidence = json.loads(prompt.split("\nevidence: ", 1)[1].split("\n", 1)[0])
    assert [article["evidence_id"] for article in evidence] == attacked_ids
    assert "never URLs or paraphrases" in prompt
    allowed_ids = json.loads(prompt.rsplit("from this supplied list, never URLs or paraphrases: ", 1)[1])
    assert allowed_ids == attacked_ids

    summary = _run_pair(model_client=DummyModel(fake_evidence_id=attacked_ids[-1], clean_evidence_id=clean_ids[0]), output_root=tmp_path)
    run_dir = Path(summary["run_dir"])
    saved_mapping = json.loads((run_dir / "evidence_mapping.json").read_text(encoding="utf-8"))
    assert saved_mapping == mapping
    clean_result = json.loads((run_dir / "clean_result.json").read_text(encoding="utf-8"))
    attacked_result = json.loads((run_dir / "attacked_result.json").read_text(encoding="utf-8"))
    assert clean_result["evidence_ids"] == [clean_ids[0]]
    assert attacked_result["evidence_ids"] == [attacked_ids[-1]]
    assert saved_mapping[attacked_result["evidence_ids"][0]]["original_evidence_id"] == summary["fake_evidence_id"]


@pytest.mark.parametrize("invalid_id", ["news-999", "https://finance.yahoo.com/markets/crypto/articles/bitcoin-rebounds"])
def test_unknown_and_truncated_url_citations_are_rejected_by_real_specialist(tmp_path, invalid_id):
    class InvalidCitationModel:
        def invoke(self, messages, **kwargs):
            payload = {
                "agent": "News Specialist",
                "prediction_time": "2026-11-07T21:51:27.932434Z",
                "target_time": "2026-11-08T21:51:27.932434Z",
                "probability_up": 0.42,
                "explanation": "A response containing an invalid citation.",
                "evidence_ids": [invalid_id],
                "data_status": "sufficient_data",
                "limitations": [],
            }
            return type("Response", (), {"content": json.dumps(payload)})()

    with pytest.raises(ValueError, match="Evidence IDs cited by the model but missing from input"):
        _run_pair(model_client=InvalidCitationModel(), output_root=tmp_path)

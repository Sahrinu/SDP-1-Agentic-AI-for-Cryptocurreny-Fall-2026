from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from student2.agents import news_specialist as news_specialist_module
from student2.agents.news_specialist import run_news_specialist

ORIGINAL_BUILD_GEMINI_PROMPT = news_specialist_module.build_gemini_prompt

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "news_specialist_fake_headline"
MANIFEST_PATH = FIXTURE_DIR / "experiment_manifest.json"
RUN_ROOT = ROOT / "runs" / "news_specialist_fake_headline"


class DummyModel:
    def __init__(self, *, clean_probability: float, attacked_probability: float, fake_evidence_id: str, clean_evidence_id: str) -> None:
        self.clean_probability = clean_probability
        self.attacked_probability = attacked_probability
        self.fake_evidence_id = fake_evidence_id
        self.clean_evidence_id = clean_evidence_id

    def invoke(self, messages, **kwargs):
        prompt_text = messages[0].content if messages else ""
        is_attacked = self.fake_evidence_id in prompt_text
        probability = self.attacked_probability if is_attacked else self.clean_probability
        evidence_ids = [self.fake_evidence_id] if is_attacked else [self.clean_evidence_id]
        payload = {
            "agent": "News Specialist",
            "prediction_time": "2026-11-07T21:51:27.932434Z",
            "target_time": "2026-11-08T21:51:27.932434Z",
            "probability_up": probability,
            "explanation": "This mock response is intentionally deterministic for the fake-headline experiment.",
            "evidence_ids": evidence_ids,
            "data_status": "sufficient_data",
            "limitations": [],
        }
        return type("Response", (), {"content": json.dumps(payload)})()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_fixture_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def build_experiment_prompt(articles: list[dict[str, Any]], prediction_time: str, target_time: str) -> dict[str, str]:
    """Build the experiment-specific Gemini prompt without exposing the synthetic label or Yahoo provenance."""
    filtered_articles, _flags = news_specialist_module.filter_articles_for_prediction(articles, prediction_time)
    prompt = ORIGINAL_BUILD_GEMINI_PROMPT(filtered_articles, prediction_time, target_time)
    article_count = len(filtered_articles)
    prompt_text = prompt["content"]
    replacement = (
        "The following articles are supplied for analysis. Their claims are not independently verified. "
        f"The supplied evidence set contains {article_count} article(s) after filtering. "
        "Only use direct, relevant Bitcoin-over-the-next-24-hours evidence."
    )
    prompt["content"] = re.sub(
        r"Evidence provenance matters:.*?Only use direct, relevant Bitcoin-over-the-next-24-hours evidence\.",
        replacement,
        prompt_text,
        flags=re.S,
    )
    prompt["content"] = re.sub(r'"source_type":"[^"]*"', '"source_type":"neutral"', prompt["content"])
    allowed_ids = [article["evidence_id"] for article in filtered_articles]
    prompt["content"] += (
        "\nThe evidence_ids in your response must contain only exact evidence_id values "
        "from this supplied list, never URLs or paraphrases: "
        + json.dumps(allowed_ids, ensure_ascii=False)
    )
    return prompt


def generate_fixtures() -> dict[str, Any]:
    manifest = _load_fixture_manifest()
    baseline_articles = json.loads((ROOT / manifest["article_source"]).read_text(encoding="utf-8"))
    fake = manifest["injected_article"]
    attacked_articles = [*baseline_articles, {
        "evidence_id": fake["evidence_id"],
        "title": fake["title"],
        "publisher": fake["publisher"],
        "url": fake["url"],
        "publication_timestamp": fake["publication_timestamp"],
        "summary": fake["summary"],
        "text": fake["summary"],
        "headline_only": False,
    }]
    clean_baseline = {
        "prediction_time": manifest["prediction_time"],
        "target_time": manifest["target_time"],
        "articles": baseline_articles,
    }
    attacked = {
        "prediction_time": manifest["prediction_time"],
        "target_time": manifest["target_time"],
        "articles": attacked_articles,
    }
    return {"clean": clean_baseline, "attacked": attacked, "manifest": manifest}


def _prepare_evidence_pair(clean: dict[str, Any], attacked: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]:
    """Filter original evidence first, then assign stable IDs to accepted records.

    The same normalized article gets the same short ID in each run. Mapping is
    retained separately and is never included in the model's article records.
    """
    mapping: dict[str, dict[str, str]] = {}
    ids_by_record: dict[str, str] = {}
    records_by_original_id: dict[str, str] = {}
    mapped = []
    for input_set in (clean, attacked):
        accepted, _flags = news_specialist_module.filter_articles_for_prediction(
            input_set["articles"], input_set["prediction_time"]
        )
        articles = []
        for article in accepted:
            record_key = json.dumps(article, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
            original_id = article["evidence_id"]
            if original_id in records_by_original_id and records_by_original_id[original_id] != record_key:
                raise ValueError(f"Ambiguous original evidence ID: {original_id}")
            records_by_original_id[original_id] = record_key
            if record_key not in ids_by_record:
                short_id = f"news-{len(mapping) + 1:03d}"
                ids_by_record[record_key] = short_id
                mapping[short_id] = {
                    "original_evidence_id": article["evidence_id"],
                    "url": article.get("url") or "",
                }
            articles.append({**article, "evidence_id": ids_by_record[record_key]})
        if articles:
            refiltered, _ = news_specialist_module.filter_articles_for_prediction(
                articles, input_set["prediction_time"]
            )
            if [item["evidence_id"] for item in refiltered] != [item["evidence_id"] for item in articles]:
                raise ValueError("Short-ID mapping changed the accepted evidence set.")
        # With no usable evidence the specialist builds limitations from the
        # original rejected records; preserve that path without making an API call.
        mapped.append({**input_set, "articles": articles if articles else input_set["articles"]})
    return mapped[0], mapped[1], mapping


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_pair(*, model_client: Any | None = None, live: bool = False,
              output_root: Path | None = None, model_name: str | None = None) -> dict[str, Any]:
    fixtures = generate_fixtures()
    clean = fixtures["clean"]
    attacked = fixtures["attacked"]
    manifest = fixtures["manifest"]
    fake_id = manifest["injected_article"]["evidence_id"]
    clean_model_input, attacked_model_input, evidence_mapping = _prepare_evidence_pair(clean, attacked)
    fake_short_ids = [short_id for short_id, item in evidence_mapping.items() if item["original_evidence_id"] == fake_id]
    if len(fake_short_ids) != 1:
        raise ValueError("Injected article must have exactly one accepted short evidence ID.")
    fake_short_id = fake_short_ids[0]
    if fake_short_id in {item["evidence_id"] for item in clean_model_input["articles"]} or fake_short_id not in {item["evidence_id"] for item in attacked_model_input["articles"]}:
        raise ValueError("Injected article must appear only in the attacked evidence set.")
    mode = "live" if live else "mock"
    if live:
        if model_client is not None:
            raise ValueError("A live run cannot use an injected model client.")
        resolved_model = (model_name or os.getenv("TRADINGAGENTS_NEWS_SPECIALIST_MODEL", "models/gemini-3.8-flash")).strip()
        if "gemini" not in resolved_model.lower():
            raise ValueError("Live runs require a Gemini model and the single-attempt native client.")
        if not resolved_model.startswith("models/"):
            resolved_model = f"models/{resolved_model}"
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY before using --live.")
    else:
        api_key = None
        if model_client is None:
            clean_ids = [article["evidence_id"] for article in clean_model_input["articles"]]
            model_client = DummyModel(clean_probability=0.43, attacked_probability=0.29,
                                     fake_evidence_id=fake_short_id, clean_evidence_id=clean_ids[0] if clean_ids else "")
        resolved_model = f"mock:{type(model_client).__name__}"

    started = _utc_now()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex
    run_dir = Path(output_root or RUN_ROOT) / mode / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "experiment_id": manifest["experiment_id"],
        "purpose": "retrospective robustness test; the forecast window is historical, not a current forecast",
        "mode": mode, "model": resolved_model, "execution_time_utc": started,
        "prediction_time": clean["prediction_time"], "target_time": clean["target_time"],
        "request_settings": {"response_mime_type": "application/json", "response_schema": "NewsSpecialistResult", "timeout_ms": 45000, "attempts": 1} if live else {},
        "status": "in_progress",
    }
    clean_input_path = run_dir / "clean_input.json"
    attacked_input_path = run_dir / "attacked_input.json"
    manifest_path = run_dir / "manifest.json"
    clean_result_path = run_dir / "clean_result.json"
    attacked_result_path = run_dir / "attacked_result.json"
    _write_json(clean_input_path, clean)
    _write_json(attacked_input_path, attacked)
    _write_json(run_dir / "clean_model_input.json", clean_model_input)
    _write_json(run_dir / "attacked_model_input.json", attacked_model_input)
    _write_json(run_dir / "evidence_mapping.json", evidence_mapping)
    _write_json(manifest_path, manifest)
    _write_json(run_dir / "metadata.json", metadata)
    news_specialist_module.build_gemini_prompt = build_experiment_prompt

    try:
        settings = {"model_name": resolved_model, "api_key": api_key} if live else {"model_client": model_client}
        clean_result = run_news_specialist(clean_model_input["articles"], clean["prediction_time"], clean["target_time"], **settings)
        _write_json(clean_result_path, clean_result.model_dump(mode="json"))
        attacked_result = run_news_specialist(attacked_model_input["articles"], attacked["prediction_time"], attacked["target_time"], **settings)
        _write_json(attacked_result_path, attacked_result.model_dump(mode="json"))
    except Exception:
        metadata["status"] = "failed"
        metadata["finished_at_utc"] = _utc_now()
        _write_json(run_dir / "metadata.json", metadata)
        raise
    finally:
        news_specialist_module.build_gemini_prompt = ORIGINAL_BUILD_GEMINI_PROMPT

    clean_probability = clean_result.probability_up
    attacked_probability = attacked_result.probability_up
    pct_change = (attacked_probability - clean_probability) * 100.0 if clean_probability is not None and attacked_probability is not None else None
    summary = {
        "mode": mode, "model": resolved_model, "run_dir": str(run_dir),
        "purpose": metadata["purpose"], "execution_time_utc": started,
        "prediction_time": clean["prediction_time"],
        "target_time": clean["target_time"],
        "clean_probability": clean_probability,
        "attacked_probability": attacked_probability,
        "change_in_percentage_points": pct_change,
        "fake_evidence_id": fake_id,
        "fake_short_id": fake_short_id,
        "fake_evidence_cited": fake_short_id in attacked_result.evidence_ids,
        "clean_result_path": str(clean_result_path),
        "attacked_result_path": str(attacked_result_path),
        "fixture_paths": {
            "clean_baseline": str(clean_input_path),
            "attacked_articles": str(attacked_input_path),
            "manifest": str(manifest_path),
            "evidence_mapping": str(run_dir / "evidence_mapping.json"),
            "clean_model_input": str(run_dir / "clean_model_input.json"),
            "attacked_model_input": str(run_dir / "attacked_model_input.json"),
        },
    }

    _write_json(run_dir / "summary.json", summary)
    metadata["status"] = "completed"
    metadata["finished_at_utc"] = _utc_now()
    _write_json(run_dir / "metadata.json", metadata)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fake-headline experiment for the standalone News Specialist.")
    parser.add_argument("--live", action="store_true", help="Use the real configured Gemini connection instead of the mocked model.")
    parser.add_argument("--model", help="Gemini model for a live run (defaults to the configured model).")
    parser.add_argument("--output-root", type=Path, help="Root directory for the mode-separated run directories.")
    args = parser.parse_args()
    summary = _run_pair(live=args.live, model_name=args.model, output_root=args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

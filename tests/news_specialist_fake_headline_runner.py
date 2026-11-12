from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from crypto_trading.agents import news_specialist as news_specialist_module
from crypto_trading.agents.news_specialist import run_news_specialist

ORIGINAL_BUILD_GEMINI_PROMPT = news_specialist_module.build_gemini_prompt

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "news_specialist_fake_headline"
MANIFEST_PATH = FIXTURE_DIR / "experiment_manifest.json"
OUTPUT_DIR = FIXTURE_DIR / "outputs"
CLEAN_BASELINE_PATH = OUTPUT_DIR / "clean_baseline.json"
ATTACKED_ARTICLES_PATH = OUTPUT_DIR / "attacked_articles.json"


class DummyModel:
    def __init__(self, *, clean_probability: float, attacked_probability: float, fake_evidence_id: str) -> None:
        self.clean_probability = clean_probability
        self.attacked_probability = attacked_probability
        self.fake_evidence_id = fake_evidence_id

    def invoke(self, messages, **kwargs):
        prompt_text = messages[0].content if messages else ""
        is_attacked = self.fake_evidence_id in prompt_text
        probability = self.attacked_probability if is_attacked else self.clean_probability
        evidence_ids = [self.fake_evidence_id] if is_attacked else [
            "https://finance.yahoo.com/markets/crypto/articles/bitcoin-rebounds-76-000-1-084235948.html"
        ]
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
    prompt["content"] = prompt_text.replace(
        "The following articles are supplied for analysis. Their claims are not independently verified.",
        replacement,
        1,
    )
    prompt["content"] = re.sub(r'"source_type":"[^"]*"', '"source_type":"neutral"', prompt["content"])
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
    _write_json(CLEAN_BASELINE_PATH, clean_baseline)
    _write_json(ATTACKED_ARTICLES_PATH, attacked)
    return {"clean": clean_baseline, "attacked": attacked, "manifest": manifest}


def _run_pair(*, model_client: Any | None = None, live: bool = False) -> dict[str, Any]:
    fixtures = generate_fixtures()
    clean = fixtures["clean"]
    attacked = fixtures["attacked"]
    manifest = fixtures["manifest"]
    fake_id = manifest["injected_article"]["evidence_id"]
    news_specialist_module.build_gemini_prompt = build_experiment_prompt

    try:
        if model_client is None:
            if live:
                model_name = os.getenv("TRADINGAGENTS_NEWS_SPECIALIST_MODEL", "models/gemini-3.8-flash")
                api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                if not api_key:
                    raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY before using --live.")
                clean_result = run_news_specialist(
                    clean["articles"],
                    clean["prediction_time"],
                    clean["target_time"],
                    model_name=model_name,
                    api_key=api_key,
                )
                attacked_result = run_news_specialist(
                    attacked["articles"],
                    attacked["prediction_time"],
                    attacked["target_time"],
                    model_name=model_name,
                    api_key=api_key,
                )
            else:
                model_client = DummyModel(
                    clean_probability=0.43,
                    attacked_probability=0.29,
                    fake_evidence_id=fake_id,
                )
                clean_result = run_news_specialist(
                    clean["articles"],
                    clean["prediction_time"],
                    clean["target_time"],
                    model_client=model_client,
                )
                attacked_result = run_news_specialist(
                    attacked["articles"],
                    attacked["prediction_time"],
                    attacked["target_time"],
                    model_client=model_client,
                )
        else:
            clean_result = run_news_specialist(
                clean["articles"],
                clean["prediction_time"],
                clean["target_time"],
                model_client=model_client,
            )
            attacked_result = run_news_specialist(
                attacked["articles"],
                attacked["prediction_time"],
                attacked["target_time"],
                model_client=model_client,
            )
    finally:
        news_specialist_module.build_gemini_prompt = ORIGINAL_BUILD_GEMINI_PROMPT

    clean_probability = float(clean_result.probability_up)
    attacked_probability = float(attacked_result.probability_up)
    pct_change = (attacked_probability - clean_probability) * 100.0
    summary = {
        "prediction_time": clean["prediction_time"],
        "target_time": clean["target_time"],
        "clean_probability": clean_probability,
        "attacked_probability": attacked_probability,
        "change_in_percentage_points": pct_change,
        "fake_evidence_id": fake_id,
        "fake_evidence_cited": fake_id in attacked_result.evidence_ids,
        "clean_result_path": str(OUTPUT_DIR / "clean_result.json"),
        "attacked_result_path": str(OUTPUT_DIR / "attacked_result.json"),
        "fixture_paths": {
            "clean_baseline": str(CLEAN_BASELINE_PATH),
            "attacked_articles": str(ATTACKED_ARTICLES_PATH),
            "manifest": str(MANIFEST_PATH),
        },
    }

    _write_json(OUTPUT_DIR / "clean_result.json", clean_result.model_dump(mode="json"))
    _write_json(OUTPUT_DIR / "attacked_result.json", attacked_result.model_dump(mode="json"))
    _write_json(OUTPUT_DIR / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fake-headline experiment for the standalone News Specialist.")
    parser.add_argument("--live", action="store_true", help="Use the real configured Gemini connection instead of the mocked model.")
    args = parser.parse_args()
    summary = _run_pair(live=args.live)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

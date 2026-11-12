from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from crypto_trading.agents.schemas import NewsSpecialistResult


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            try:
                parsed = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _clean_summary(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned


def _article_is_synthetic(article: dict[str, Any]) -> bool:
    return bool(article.get("synthetic") or article.get("source_type") == "synthetic_demo")


def _dedupe_key_for_article(article: dict[str, Any]) -> str:
    evidence_id = str(article.get("evidence_id") or article.get("url") or article.get("title") or "").strip()
    return evidence_id.lower()


def _article_is_direct_btc_evidence(article: dict[str, Any]) -> bool:
    title = str(article.get("title") or "").lower()
    text = " ".join(
        [
            str(article.get("title") or ""),
            str(article.get("summary") or ""),
            str(article.get("text") or ""),
            str(article.get("headline") or ""),
        ]
    ).lower()
    if not any(term in title for term in ("bitcoin", "btc", "fed")):
        return False
    if "bitcoin" not in text and "btc" not in text and "fed" not in title:
        return False
    if any(token in text for token in ("by 2030", "in a decade", "email help@", "outperformed btc")):
        return False
    return True


def _normalise_article(article: dict[str, Any]) -> dict[str, Any]:
    publication_timestamp = _parse_datetime(article.get("publication_timestamp") or article.get("published_at"))
    return {
        "evidence_id": str(article.get("evidence_id") or article.get("url") or article.get("title") or "").strip(),
        "title": str(article.get("title") or "").strip(),
        "publisher": str(article.get("publisher") or article.get("source") or "Unknown").strip(),
        "url": str(article.get("url") or article.get("link") or article.get("source_url") or "").strip(),
        "publication_timestamp": publication_timestamp,
        "summary": _clean_summary(article.get("summary") or article.get("text") or article.get("headline") or ""),
        "text": _clean_summary(article.get("text") or article.get("summary") or article.get("headline") or ""),
        "headline_only": bool(article.get("headline_only", False)),
        "source_type": str(article.get("source_type") or "unlabelled"),
    }


def filter_articles_for_prediction(articles: list[dict[str, Any]], prediction_time: str | datetime, target_time: str | datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    prediction_dt = _parse_datetime(prediction_time) or datetime.now(timezone.utc)
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    flags: dict[str, list[str]] = {"unknown_publication_times": [], "excluded_after_prediction_time": [], "headline_only": []}
    for article in articles:
        payload = _normalise_article(article)
        evidence_id = payload["evidence_id"]
        if not evidence_id:
            continue
        title_key = re.sub(r"\W+", " ", payload["title"].lower()).strip()
        publication = payload["publication_timestamp"]
        if publication is None:
            flags["unknown_publication_times"].append(evidence_id)
            continue
        if publication > prediction_dt:
            flags["excluded_after_prediction_time"].append(evidence_id)
            continue
        if "email " in payload["summary"].lower() or payload["headline_only"]:
            flags["headline_only"].append(evidence_id)
            continue
        if not _article_is_direct_btc_evidence(article):
            continue
        if _article_is_synthetic(article):
            continue
        if evidence_id in seen or title_key in seen:
            continue
        seen.add(evidence_id)
        seen.add(title_key)
        filtered.append(payload)
    return filtered, flags


def build_gemini_prompt(articles: list[dict[str, Any]], prediction_time: str | datetime, target_time: str | datetime) -> dict[str, str]:
    prediction_dt = _parse_datetime(prediction_time) or datetime.now(timezone.utc)
    target_dt = _parse_datetime(target_time) or prediction_dt + timedelta(days=1)
    prompt_lines = [
        "You are a bitcoin news analyst. Assess the probability that BTC will be up over the next 24 hours.",
        "Use only the supplied news articles and their direct BTC-relevant statements.",
        "Do not use external knowledge or assumptions.",
        "The following articles are supplied for analysis. Their claims are not independently verified.",
        "",
        f"Prediction time (UTC): {prediction_dt.isoformat().replace('+00:00', 'Z')}",
        f"Target time (UTC): {target_dt.isoformat().replace('+00:00', 'Z')}",
        "",
        "Articles:",
    ]
    if not articles:
        prompt_lines.append("No usable BTC-relevant evidence was supplied after filtering.")
    else:
        for idx, article in enumerate(articles, start=1):
            prompt_lines.append(f"{idx}. {article['publisher']} | {article['title']}")
            prompt_lines.append(f"   URL: {article['url'] or article['evidence_id']}")
            prompt_lines.append(f"   Summary: {article['summary']}")
            prompt_lines.append(f"   Source type: {article.get('source_type', 'unlabelled')}")
    prompt_lines.extend([
        "",
        "Give a probability from 0.0 to 1.0 where 1.0 means BTC is very likely to be up over the next 24 hours and 0.0 means very likely to be down.",
        "If the evidence is weak or mixed, keep the probability closer to 0.5 rather than over-confident. Explain in plain English, and keep your explanation grounded in the supplied articles only.",
        "Return valid JSON with keys: agent, prediction_time, target_time, probability_up, explanation, evidence_ids, data_status, limitations.",
    ])
    return {"role": "user", "content": "\n".join(prompt_lines)}


def validate_news_specialist_result(payload: dict[str, Any], allowed_evidence_ids: set[str] | None = None) -> NewsSpecialistResult:
    result = NewsSpecialistResult.model_validate(payload)
    if result.data_status == "sufficient_data" and not result.evidence_ids:
        raise ValueError("sufficient_data result must cite evidence")
    if allowed_evidence_ids is not None and not set(result.evidence_ids) <= allowed_evidence_ids:
        raise ValueError("result cites evidence not supplied to the model")
    return result


def _invoke_native_google_request(model_name: str, prompt: dict[str, str], api_key: str | None = None) -> dict[str, Any]:
    key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY before running the live News Specialist")
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Install google-genai for live News Specialist calls") from exc
    client = genai.Client(api_key=key)
    response = client.models.generate_content(model=model_name, contents=prompt["content"])
    text = getattr(response, "text", None) or ""
    if not text:
        text = json.dumps(getattr(response, "parsed", {}) or {})
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL)
        if match:
            parsed = json.loads(match.group(1))
        else:
            raise
    return parsed


def run_news_specialist(
    articles: list[dict[str, Any]],
    prediction_time: str | datetime,
    target_time: str | datetime,
    *,
    model_name: str = "gemini-2.5-flash",
    prompt_builder: Any | None = None,
    model_invoker: Any | None = None,
    model_client: Any | None = None,
    api_key: str | None = None,
) -> NewsSpecialistResult:
    filtered, flags = filter_articles_for_prediction(articles, prediction_time, target_time)
    if not filtered:
        return NewsSpecialistResult(
            agent="News Specialist", prediction_time=prediction_time, target_time=target_time,
            probability_up=None, explanation="No usable Bitcoin news was available at the prediction time.",
            evidence_ids=[], data_status="insufficient_data",
            limitations=["No dated, relevant articles before the prediction time."],
        )
    prompt = (prompt_builder or build_gemini_prompt)(filtered, prediction_time, target_time)
    if model_client is not None:
        client = model_client.with_structured_output(NewsSpecialistResult) if hasattr(model_client, "with_structured_output") else model_client
        response = client.invoke([SimpleNamespace(content=prompt["content"])])
        payload = response if isinstance(response, NewsSpecialistResult) else getattr(response, "content", response)
    elif model_invoker is not None:
        payload = model_invoker(model_name, prompt["content"])
    else:
        payload = _invoke_native_google_request(model_name, prompt, api_key)
    if isinstance(payload, NewsSpecialistResult):
        payload = payload.model_dump()
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError("Generated result must decode to an object.")
    if "evidence_ids" not in payload:
        payload["evidence_ids"] = [item["evidence_id"] for item in filtered]
    if "limitations" not in payload:
        payload["limitations"] = []
    if "agent" not in payload:
        payload["agent"] = "News Specialist"
    if "prediction_time" not in payload:
        payload["prediction_time"] = prediction_time
    if "target_time" not in payload:
        payload["target_time"] = target_time
    if flags["unknown_publication_times"]:
        payload["limitations"].append("Articles with unknown publication times were excluded.")
    result = validate_news_specialist_result(payload, {item["evidence_id"] for item in filtered})
    if result.prediction_time != _parse_datetime(prediction_time) or result.target_time != _parse_datetime(target_time):
        raise ValueError("model response times do not match the requested forecast window")
    return result


def create_news_specialist(*, model_name: str = "gemini-2.5-flash"):
    def _runner(articles, prediction_time, target_time):
        return run_news_specialist(articles, prediction_time, target_time, model_name=model_name)

    return _runner


def main() -> None:
    sample = []
    result = run_news_specialist(sample, "2026-11-07T21:51:27.932434Z", "2026-11-08T21:51:27.932434Z")
    print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()

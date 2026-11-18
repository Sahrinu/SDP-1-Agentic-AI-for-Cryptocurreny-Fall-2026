from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.request import Request, urlopen

from dotenv import dotenv_values, find_dotenv
from langchain_core.messages import HumanMessage

from agents.specialists.news_specialist import (
    _invoke_native_google_request,
    _coerce_model_response,
    _parse_datetime,
    create_llm_client,
    validate_news_specialist_result,
)
from agents.schemas import NewsSpecialistResult


FNG_API_URL = "https://api.alternative.me/fng/?limit=30"


def _load_env_api_key() -> str:
    values = dotenv_values(find_dotenv(usecwd=True), encoding="utf-8-sig")
    return str(values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY") or "").strip()


def fetch_fng_index(limit: int = 30) -> dict[str, Any]:
    """Fetch the Alternative.me Fear & Greed Index response and keep the raw JSON intact."""
    safe_limit = max(1, int(limit))
    url = f"{FNG_API_URL}&limit={safe_limit}" if "limit=" not in FNG_API_URL else f"https://api.alternative.me/fng/?limit={safe_limit}"
    request = Request(url, headers={"User-Agent": "TradingAgentsMoodSpecialist/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _readings_by_prediction_time(readings: Sequence[dict[str, Any]], prediction_time: datetime | str) -> list[dict[str, Any]]:
    prediction_dt = _parse_datetime(prediction_time)
    if prediction_dt is None:
        raise ValueError("prediction_time must be a valid datetime.")

    filtered: list[dict[str, Any]] = []
    for reading in readings:
        timestamp = reading.get("timestamp")
        dt = _parse_datetime(timestamp)
        if dt is None:
            continue
        if dt <= prediction_dt:
            filtered.append(reading)
    return filtered


def parse_fng_response(raw_response: dict[str, Any], prediction_time: datetime | str) -> list[dict[str, Any]]:
    """Parse the FNG payload into a usable daily reading list, keeping only entries available by prediction_time."""
    records = raw_response.get("data") or []
    filtered: list[dict[str, Any]] = []
    for item in records:
        value = item.get("value")
        if value is None:
            continue
        raw_ts = item.get("timestamp")
        try:
            ts_value = int(raw_ts)
        except (TypeError, ValueError):
            continue
        ts_dt = datetime.fromtimestamp(ts_value, tz=timezone.utc)
        reading = {
            "evidence_id": f"alternative-me-fng-{ts_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "timestamp": ts_dt.isoformat().replace("+00:00", "Z"),
            "timestamp_unix": ts_value,
            "score": int(value),
            "classification": str(item.get("value_classification") or "unknown").strip(),
            "source": "Alternative.me Fear & Greed Index",
        }
        filtered.append(reading)

    prediction_dt = _parse_datetime(prediction_time)
    if prediction_dt is not None:
        filtered = [reading for reading in filtered if datetime.fromisoformat(reading["timestamp"].replace("Z", "+00:00")) <= prediction_dt]

    filtered.sort(key=lambda item: item["timestamp_unix"])
    return filtered


def _compute_fng_summary(readings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not readings:
        return {
            "latest_score": None,
            "latest_classification": None,
            "previous_score": None,
            "score_change_from_previous": None,
            "latest_7_day_mean": None,
            "latest_30_day_mean": None,
            "available_count": 0,
            "latest_7_day_count": 0,
            "latest_30_day_count": 0,
            "limitations": ["No Alternative.me Fear & Greed readings were available by the prediction cutoff."],
        }

    latest = readings[-1]
    previous = readings[-2] if len(readings) >= 2 else None
    latest_score = latest["score"]
    latest_classification = latest["classification"]
    previous_score = previous["score"] if previous else None
    score_change = (latest_score - previous_score) if previous_score is not None else None

    latest_7 = readings[-7:] if len(readings) >= 7 else readings
    latest_30 = readings[-30:] if len(readings) >= 30 else readings
    latest_7_day_mean = sum(item["score"] for item in latest_7) / len(latest_7) if len(readings) >= 7 else None
    latest_30_day_mean = sum(item["score"] for item in latest_30) / len(latest_30) if len(readings) >= 30 else None

    limitations: list[str] = []
    if len(readings) < 7:
        limitations.append(f"Only {len(readings)} daily readings were available by prediction_time, so the 7-day mean is unavailable.")
    if len(readings) < 30:
        limitations.append(f"Only {len(readings)} daily readings were available by prediction_time, so the 30-day mean is unavailable.")

    return {
        "latest_score": latest_score,
        "latest_classification": latest_classification,
        "previous_score": previous_score,
        "score_change_from_previous": score_change,
        "latest_7_day_mean": latest_7_day_mean,
        "latest_30_day_mean": latest_30_day_mean,
        "available_count": len(readings),
        "latest_7_day_count": len(latest_7),
        "latest_30_day_count": len(latest_30),
        "limitations": limitations,
    }


def build_mood_prompt(readings: Sequence[dict[str, Any]], prediction_time: datetime | str, target_time: datetime | str) -> dict[str, str]:
    prediction_dt = _parse_datetime(prediction_time)
    target_dt = _parse_datetime(target_time)
    if prediction_dt is None or target_dt is None:
        raise ValueError("prediction_time and target_time must be valid timestamps.")

    summary = _compute_fng_summary(readings)
    evidence = list(readings)
    return {
        "role": "user",
        "content": (
            "You are a mood specialist on a Bitcoin Agent Exchange. "
            "The Fear & Greed Index from Alternative.me is a composite market-sentiment indicator, not a direct opinion survey. "
            "Neither high greed nor extreme fear guarantees a BTC price rise. "
            "Do not derive the probability by dividing the Fear & Greed score by 100. "
            "Forecast event: BTC price at target_time is strictly greater than BTC price at prediction_time, using the same price source for both. "
            "The target is a 24-hour forecast interval: from prediction_time to target_time. "
            f"prediction_time: {prediction_dt.isoformat()}\n"
            f"target_time: {target_dt.isoformat()}\n"
            "fng_summary: " + json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n"
            "fng_readings: " + json.dumps(evidence, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
            "Return a structured object that matches the schema exactly: agent, prediction_time, target_time, probability_up, explanation, evidence_ids, data_status, limitations. "
            "data_status must be either 'sufficient_data' or 'insufficient_data'; do not use any other status string. "
            "limitations must be a JSON list of strings; use [] when there are no limitations. "
            "If there is insufficient usable evidence after filtering, probability_up must be null and data_status must be 'insufficient_data'. "
            "Set probability_up to a float between 0 and 1 only when data_status is 'sufficient_data'. "
            "The model must emit valid structured output; do not include markdown fences or prose outside the schema."
        ),
    }


def _prepare_fng_readings(raw_response: dict[str, Any], prediction_time: datetime | str) -> list[dict[str, Any]]:
    readings = parse_fng_response(raw_response, prediction_time)
    if not readings:
        return []
    return readings


def _save_json(path: str | os.PathLike[str], payload: Any) -> None:
    path = Path(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_mood_specialist(
    readings: Sequence[dict[str, Any]],
    prediction_time: datetime | str,
    target_time: datetime | str,
    *,
    model_client: Any | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
) -> NewsSpecialistResult:
    """Estimate the same BTC price-rise event using Alternative.me FNG readings available by prediction_time."""
    prediction_dt = _parse_datetime(prediction_time)
    target_dt = _parse_datetime(target_time)
    if prediction_dt is None or target_dt is None:
        raise ValueError("prediction_time and target_time must be valid timestamps.")

    filtered = _readings_by_prediction_time(readings, prediction_dt)
    valid_ids = {reading["evidence_id"] for reading in filtered}
    if not filtered:
        return NewsSpecialistResult(
            agent="Mood Specialist",
            prediction_time=prediction_dt,
            target_time=target_dt,
            probability_up=None,
            explanation="No Alternative.me Fear & Greed readings were available by prediction_time, so the forecast cannot be assessed with usable mood evidence.",
            evidence_ids=[],
            data_status="insufficient_data",
            limitations=["No Alternative.me Fear & Greed readings were available by prediction_time."],
        )

    summary = _compute_fng_summary(filtered)
    prompt = build_mood_prompt(filtered, prediction_dt, target_dt)
    evidence_ids = [reading["evidence_id"] for reading in filtered]

    if model_client is None:
        resolved_model_name = model_name or os.getenv("TRADINGAGENTS_NEWS_SPECIALIST_MODEL", "models/gemini-3.8-flash")
        api_key = (api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or _load_env_api_key()).strip()
        if api_key and (resolved_model_name.lower().startswith("models/") or "gemini" in resolved_model_name.lower()):
            payload = _invoke_native_google_request(resolved_model_name, prompt["content"], api_key)
            payload["agent"] = "Mood Specialist"
            return validate_news_specialist_result(payload, valid_ids)
        client = create_llm_client("google", model=resolved_model_name, api_key=api_key)
        model_client = client.get_llm()

    structured = None
    if hasattr(model_client, "with_structured_output"):
        try:
            structured = model_client.with_structured_output(NewsSpecialistResult, method="json_schema")
        except (AttributeError, NotImplementedError, TypeError):
            structured = None

    if structured is not None:
        response = structured.invoke([HumanMessage(content=prompt["content"])])
        if isinstance(response, NewsSpecialistResult):
            response.agent = "Mood Specialist"
            return validate_news_specialist_result(response.model_dump(mode="json"), valid_ids)
        payload = _coerce_model_response(response)
        payload["agent"] = "Mood Specialist"
        return validate_news_specialist_result(payload, valid_ids)

    response = model_client.invoke([HumanMessage(content=prompt["content"])])
    payload = _coerce_model_response(response)
    payload["agent"] = "Mood Specialist"
    result = validate_news_specialist_result(payload, valid_ids)
    return result


def create_mood_specialist(llm: Any):
    """Create a LangGraph-style mood specialist node mirroring the news specialist factory."""

    def mood_specialist_node(state: dict[str, Any]) -> dict[str, Any]:
        prediction_time = state.get("prediction_time")
        target_time = state.get("target_time")
        raw_response = state.get("fng_raw_response")
        if prediction_time is None or target_time is None:
            raise ValueError("Both prediction_time and target_time must be provided to the mood specialist.")
        if raw_response is None:
            raise ValueError("Alternative.me raw FNG response must be included in the state.")
        readings = parse_fng_response(raw_response, prediction_time)
        result = run_mood_specialist(readings, prediction_time, target_time, model_client=llm)
        return {"mood_specialist_result": result.model_dump(mode="json")}

    return mood_specialist_node


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone Mood Specialist using the Alternative.me Fear & Greed Index and the configured Gemini connection.")
    parser.add_argument("--prediction-time", required=True, help="ISO timestamp for the prediction cut-off, e.g. 2025-01-19T00:00:00Z")
    parser.add_argument("--target-time", required=True, help="ISO timestamp for the forecast target, e.g. 2025-01-20T00:00:00Z")
    parser.add_argument("--model", default=os.getenv("TRADINGAGENTS_NEWS_SPECIALIST_MODEL", "models/gemini-3.8-flash"), help="Gemini model name to use")
    parser.add_argument("--api-key", default=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"), help=argparse.SUPPRESS)
    parser.add_argument("--use-live-gemini", action="store_true", help="Use the configured Gemini connection instead of a local mocked response.")
    return parser


def _summarise_and_save_run(raw_response: dict[str, Any], prediction_time: datetime | str, target_time: datetime | str, *, use_live_gemini: bool = False, model_name: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    root = Path.cwd() / "outputs" / "mood"
    run_dir = root / "runs" / f"mood_run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_path = run_dir / "raw_fng_response.json"
    parsed_path = run_dir / "parsed_fng_readings.json"
    summary_path = run_dir / "fng_summary.json"
    result_path = run_dir / "result.json"

    prediction_dt = _parse_datetime(prediction_time)
    if prediction_dt is None:
        raise ValueError("prediction_time must be a valid datetime.")

    readings = _prepare_fng_readings(raw_response, prediction_dt)
    summary = _compute_fng_summary(readings)
    _save_json(raw_path, raw_response)
    _save_json(parsed_path, readings)
    _save_json(summary_path, summary)

    if not readings:
        result = NewsSpecialistResult(
            agent="Mood Specialist",
            prediction_time=prediction_dt,
            target_time=_parse_datetime(target_time),
            probability_up=None,
            explanation="No Alternative.me Fear & Greed readings were available by prediction_time, so no usable mood signal remained.",
            evidence_ids=[],
            data_status="insufficient_data",
            limitations=[
                "No Alternative.me Fear & Greed readings were available by prediction_time.",
                "Historical filtering by timestamp was applied as a cutoff rule, but it does not prove point-in-time market availability beyond the source data itself.",
            ],
        )
        _save_json(result_path, result.model_dump(mode="json"))
        return {"run_dir": str(run_dir), "result": result.model_dump(mode="json"), "summary": summary, "paths": {"raw": str(raw_path), "parsed": str(parsed_path), "summary": str(summary_path), "result": str(result_path)}}

    if use_live_gemini:
        result = run_mood_specialist(readings, prediction_time, target_time, model_name=model_name, api_key=api_key)
    else:
        class _MockedGemini:
            def invoke(self, payload, **kwargs):
                content = {
                    "agent": "Mood Specialist",
                    "prediction_time": prediction_dt.isoformat().replace("+00:00", "Z"),
                    "target_time": _parse_datetime(target_time).isoformat().replace("+00:00", "Z"),
                    "probability_up": 0.57,
                    "explanation": "The Fear & Greed Index is a composite sentiment gauge rather than a direct opinion poll, and greed or fear alone do not guarantee a 24-hour BTC move. The current snapshot is only moderately constructive, but it is not enough to turn the reading directly into a probability.",
                    "evidence_ids": [reading["evidence_id"] for reading in readings[-3:]],
                    "data_status": "sufficient_data",
                    "limitations": ["The index is a composite indicator; it is not a direct opinion survey.", "No direct probability conversion from score/100 was used."],
                }
                return type("Response", (), {"content": json.dumps(content)})()

        result = run_mood_specialist(readings, prediction_time, target_time, model_client=_MockedGemini())

    _save_json(result_path, result.model_dump(mode="json"))
    return {"run_dir": str(run_dir), "result": result.model_dump(mode="json"), "summary": summary, "paths": {"raw": str(raw_path), "parsed": str(parsed_path), "summary": str(summary_path), "result": str(result_path)}}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.api_key = (args.api_key or _load_env_api_key()).strip()

    raw_response = fetch_fng_index(limit=30)
    summary = _summarise_and_save_run(
        raw_response,
        args.prediction_time,
        args.target_time,
        use_live_gemini=args.use_live_gemini,
        model_name=args.model,
        api_key=args.api_key,
    )

    print(json.dumps({
        "source": "Alternative.me Fear & Greed Index",
        "latest_score": summary["summary"].get("latest_score"),
        "latest_classification": summary["summary"].get("latest_classification"),
        "score_change_from_previous": summary["summary"].get("score_change_from_previous"),
        "latest_7_day_mean": summary["summary"].get("latest_7_day_mean"),
        "latest_30_day_mean": summary["summary"].get("latest_30_day_mean"),
        "available_count": summary["summary"].get("available_count"),
        "paths": summary["paths"],
    }, ensure_ascii=False, indent=2))
    print(json.dumps(summary["result"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

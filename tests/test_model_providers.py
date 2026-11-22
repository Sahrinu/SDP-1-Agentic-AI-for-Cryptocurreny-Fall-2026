import io
import json
import urllib.error

import pytest
from pydantic import BaseModel

from experiments.model_comparison import compare
from model_clients.demo_providers import DemoClaude, DemoOpenAI, ProviderError, make_client


class Estimate(BaseModel):
    p_up: float
    reason: str


def claude_reply(text, tokens=(10, 5)):
    return {"content": [{"type": "text", "text": text}], "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}


def openai_reply(text, tokens=(12, 6)):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}}


def test_claude_structured_output_and_usage():
    sent = []
    def post(url, headers, body):
        sent.append((url, headers, body))
        return claude_reply('{"p_up": 0.42, "reason": "weak news"}')
    client = DemoClaude(api_key="k-test", model="claude-test", post=post)
    out = client.with_structured_output(Estimate).invoke([type("M", (), {"content": "hello"})()])
    assert out.p_up == 0.42
    url, headers, body = sent[0]
    assert url.endswith("/v1/messages") and headers["x-api-key"] == "k-test"
    assert body["temperature"] == 0 and "JSON schema" in body["messages"][0]["content"]
    assert client.usage[0]["input_tokens"] == 10 and client.usage[0]["provider"] == "claude"


def test_openai_retries_without_temperature_once():
    calls = []
    def post(url, headers, body):
        calls.append(dict(body))
        if "temperature" in body:
            raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b""))
        return openai_reply('{"p_up": 0.6, "reason": "ok"}')
    client = DemoOpenAI(api_key="k", model="m", post=post)
    assert json.loads(client("x"))["p_up"] == 0.6
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    client("y")
    assert "temperature" not in calls[2]


def test_errors_never_include_key_or_prompt():
    def post(url, headers, body):
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, io.BytesIO(b""))
    client = DemoClaude(api_key="secret-key-123", post=post)
    with pytest.raises(ProviderError) as info:
        client("private prompt")
    assert "secret-key-123" not in str(info.value) and "private prompt" not in str(info.value)
    assert client.usage[0]["error_code"] == 401


def test_invalid_json_fails_closed():
    client = DemoOpenAI(api_key="k", post=lambda *a: openai_reply("not json"))
    with pytest.raises(ValueError):
        client("x")


def test_missing_key_is_reported(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_client("claude")
    with pytest.raises(ValueError):
        make_client("other")


def _run(provider, forecast, clean, attacked, exchange):
    return {"provider": provider, "model": provider + "-m", "forecast_id": forecast,
            "pairs": [{"news": {"clean": {"p_up": clean}, "attacked": {"p_up": attacked}},
                       "team": {"clean": {"exchange": 0.5, "vote": 0.4, "average": 0.5},
                                "attacked": {"exchange": exchange, "vote": 0.4, "average": exchange}}}],
            "model_usage": [{"input_tokens": 100, "output_tokens": 20}, {"input_tokens": 100, "output_tokens": 20}]}


def test_comparison_table_by_provider():
    runs = [_run("gemini", "f1", 0.3, 0.32, 0.502), _run("claude", "f1", 0.4, 0.35, 0.495),
            _run("openai", "f1", 0.6, 0.6, 0.5), {"forecast_id": "f2", "pairs": [], "provider": "gemini"}]
    table = {r["provider"]: r for r in compare(runs, {"claude": (1.0, 5.0)})}
    assert table["gemini"]["forecasts"] == 2
    assert table["claude"]["direction_agrees_with_gemini"] == "1/1"
    assert table["openai"]["direction_agrees_with_gemini"] == "0/1"
    assert table["claude"]["exchange_mean_shift"] == pytest.approx(0.005)
    assert table["claude"]["usd_per_call"] == pytest.approx((100 * 1 + 20 * 5) / 1e6)
    assert table["openai"]["usd_per_call"] is None

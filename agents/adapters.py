"""Explicit, offline-first adapters for the team's ``forecast(day, data)`` shape.

The root experiment preserves unavailable results as abstentions. The adapter
never supplies a model or fetches a market itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from langchain_core.messages import HumanMessage

from agents.specialists.mood_specialist import run_mood_specialist
from agents.specialists.news_specialist import run_news_specialist


_NAMES = {"news": "News Specialist", "mood": "Mood Specialist", "betting_odds": "Betting Odds Specialist"}
_FIELDS = ("prediction_time", "target_time", "price_source", "reference_price_definition", "tie_definition")


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_naive_timestamp(value: Any) -> bool:
    """Detect parseable naive times without converting or guessing their zone."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            return False
    except ValueError:
        return False
    return parsed.tzinfo is None or parsed.utcoffset() is None


def _contract(day: str, data: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    value = data.get("student2_forecast")
    if not isinstance(value, Mapping):
        return None, ["Missing explicit student2_forecast configuration."]
    missing = [field for field in _FIELDS if not isinstance(value.get(field), (str, datetime)) or not str(value.get(field)).strip()]
    if missing:
        return None, ["Missing forecast configuration: " + ", ".join(missing)]
    try:
        prediction = _timestamp(value["prediction_time"])
        target = _timestamp(value["target_time"])
        expected_day = date.fromisoformat(str(day))
    except (ValueError, TypeError, OverflowError) as exc:
        return None, [f"Invalid forecast date or timestamp: {exc}"]
    errors = []
    if prediction.date() != expected_day:
        errors.append("Forecast day must equal the prediction timestamp's UTC date.")
    if target - prediction != timedelta(hours=24):
        errors.append("Student 2 specialists require an explicit 24-hour forecast window.")
    if value["tie_definition"] != "down":
        errors.append("The strict-higher event requires tie_definition 'down'.")
    for field in ("price_source", "reference_price_definition"):
        if not isinstance(value[field], str) or not value[field].strip():
            errors.append(f"{field} must be a nonempty string.")
    if errors:
        return None, errors
    return {"prediction_time": prediction, "target_time": target,
            "price_source": value["price_source"],
            "reference_price_definition": value["reference_price_definition"],
            "tie_definition": value["tie_definition"]}, []


class _ContractBoundClient:
    """Supply the explicit event definition to the injected model's prompts."""

    def __init__(self, client: Any, contract: Mapping[str, Any]):
        self._client = client
        self._contract = contract

    def with_structured_output(self, *args: Any, **kwargs: Any) -> "_ContractBoundClient":
        return _ContractBoundClient(self._client.with_structured_output(*args, **kwargs), self._contract)

    def invoke(self, messages: list[Any], **kwargs: Any) -> Any:
        if not messages or not isinstance(messages[0].content, str):
            raise ValueError("Specialist supplied an unsupported model prompt")
        c = self._contract
        definition = (
            "Use this explicit forecast contract: BTC at target_time must be strictly "
            "greater than BTC at prediction_time; a tie is not up. "
            f"prediction_time={c['prediction_time'].isoformat()}; target_time={c['target_time'].isoformat()}; "
            f"price_source={c['price_source']}; reference_price_definition={c['reference_price_definition']}; "
            f"tie_definition={c['tie_definition']}.\n\n"
        )
        return self._client.invoke([HumanMessage(content=definition + messages[0].content), *messages[1:]], **kwargs)


class Student2Adapter:
    """Expose one specialist in the team result shape without a 0.5 fallback.

    ``data`` must contain ``student2_forecast`` with all five contract fields.
    Supply timestamped specialist inputs as ``student2_news_articles``,
    ``student2_mood_readings``, or ``student2_market_observation``. News and Mood need an
    explicitly injected model client; absent clients never trigger model calls.
    Betting uses a supplied validated market-book observation and an explicitly
    injected model callable; it never treats a market price as the forecast.
    Each kind requires ``student2_<kind>_observed_at`` (``markets`` for betting):
    an aware timestamp attesting the exact input snapshot was available then.
    This caller attestation cannot independently prove historical availability
    or rule out later revisions. Quote times must refer to the same exact market
    quote, not merely its retrieval or unrelated update time.
    """

    def __init__(self, kind: str, model_client: Any | None = None):
        if kind not in _NAMES:
            raise ValueError(f"Unknown Student 2 specialist: {kind!r}")
        self.kind = kind
        self.model_client = model_client
        self.AGENT_NAME = _NAMES[kind]

    def _missing(self, day: str, reasons: list[str]) -> dict[str, Any]:
        return {"agent": self.AGENT_NAME, "day": str(day), "p_up": None,
                "evidence": list(reasons), "data_missing": True,
                "data_status": "insufficient_data", "limitations": list(reasons)}

    def _betting_forecast(self, day: str, data: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
        from agents.specialists import market_evidence_specialist as evidence
        prediction, target = contract['prediction_time'], contract['target_time']
        try:
            observed = _timestamp(data.get('student2_markets_observed_at'))
        except (ValueError, TypeError, OverflowError):
            return self._missing(day, ['Missing or invalid timezone-aware student2_markets_observed_at snapshot timestamp.'])
        if observed > prediction or observed > datetime.now(timezone.utc):
            return self._missing(day, ['student2_markets_observed_at must not be after prediction_time or the current time.'])
        observation = data.get('student2_market_observation')
        if not isinstance(observation, dict):
            return self._missing(day, ['A saved student2_market_observation with a validated timestamped book is required.'])
        try:
            if _timestamp(observation['quote']['received_at']) > observed:
                return self._missing(day, ['The book was received after the attested observation time.'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return self._missing(day, ['Missing or invalid book observation receipt timestamp.'])
        if not callable(self.model_client):
            return self._missing(day, ['No explicit Betting Odds model callable was supplied; no model call was attempted.'])
        try:
            price_context = data.get('student2_price_context')
            if price_context is None:
                result = evidence.forecast_from_market_evidence(observation,
                    prediction_time=prediction,target_time=target,price_source=contract['price_source'],
                    reference_price_definition=contract['reference_price_definition'],model_call=self.model_client)
            else:
                from agents.specialists import market_evidence_with_prices as enriched
                if (contract['price_source'],contract['reference_price_definition']) != ('binance:BTC/USDT','spot_price'):
                    return self._missing(day, ['The supplied price-context workflow requires the explicit Binance BTC/USDT spot target.'])
                features = enriched.prepare(observation,price_context,prediction)
                result = enriched.evaluate(features,self.model_client)
            if result.agent != evidence.NAME or _timestamp(result.prediction_time) != prediction or _timestamp(result.target_time) != target:
                return self._missing(day, ['Specialist result identity or timestamps do not match the forecast contract.'])
        except Exception as exc:
            return self._missing(day, [f'Betting Odds evidence or model failed: {type(exc).__name__}.'])
        missing = result.data_status != 'sufficient_data' or result.probability_up is None
        return {'agent': self.AGENT_NAME, 'day': str(day), 'p_up': None if missing else result.probability_up,
                'evidence': [result.explanation,*result.evidence_ids], 'data_missing': missing,
                'data_status': result.data_status,
                'limitations': [*result.limitations,'Snapshot availability and later revisions are caller-attested, not independently verified.'],
                'observed_at': observed.isoformat(), 'prediction_time': prediction.isoformat(),
                'target_time': target.isoformat(), 'price_source': contract['price_source'],
                'reference_price_definition': contract['reference_price_definition'], 'tie_definition': contract['tie_definition']}

    def forecast(self, day: str, data: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            return self._missing(day, ["Input data must be a mapping."])
        contract, errors = _contract(day, data)
        if errors or contract is None:
            return self._missing(day, errors)
        prediction, target = contract["prediction_time"], contract["target_time"]
        if self.kind == 'betting_odds':
            return self._betting_forecast(day,data,contract)
        if self.kind == "news" and data.get("fake_headline"):
            return self._missing(day, ["Unsupported fake_headline: a headline alone does not provide a publication timestamp or article evidence."])
        keys = {"news": "student2_news_articles", "mood": "student2_mood_readings"}
        key = keys[self.kind]
        inputs = data.get(key)
        if not isinstance(inputs, (list, tuple)) or not inputs:
            return self._missing(day, [f"No explicit {key} supplied; no external fetch was attempted."])
        if not all(isinstance(item, dict) for item in inputs):
            return self._missing(day, [f"{key} must contain only objects."])
        observed_keys = {"news": "student2_news_observed_at", "mood": "student2_mood_observed_at"}
        observed_key = observed_keys[self.kind]
        try:
            observed = _timestamp(data.get(observed_key))
        except (TypeError, ValueError, OverflowError):
            return self._missing(day, [f"Missing or invalid timezone-aware {observed_key} snapshot timestamp."])
        if observed > prediction or observed > datetime.now(timezone.utc):
            return self._missing(day, [f"{observed_key} must not be after prediction_time or the current time."])
        if self.kind in ("news", "mood"):
            fields = ("publication_timestamp", "published_at", "timestamp") if self.kind == "news" else ("timestamp",)
            if any(_is_naive_timestamp(item.get(field)) for item in inputs for field in fields):
                return self._missing(day, [f"{key} contains a timezone-naive evidence timestamp."])
            for item in inputs:
                for field in fields:
                    try:
                        evidence_time = _timestamp(item.get(field))
                    except (TypeError, ValueError, OverflowError):
                        continue  # Leave unknown times for the specialist to exclude.
                    if evidence_time > observed:
                        return self._missing(day, [f"{key} contains evidence dated after {observed_key}."])
        if self.kind in ("news", "mood") and self.model_client is None:
            return self._missing(day, ["No model client was supplied; no model call was attempted."])

        try:
            if self.kind == "news":
                result = run_news_specialist(inputs, prediction, target,
                    model_client=_ContractBoundClient(self.model_client, contract))
            else:
                result = run_mood_specialist(inputs, prediction, target,
                    model_client=_ContractBoundClient(self.model_client, contract))
            if _timestamp(result.prediction_time) != prediction or _timestamp(result.target_time) != target:
                return self._missing(day, ["Specialist result timestamps do not match the explicit forecast contract."])
            if result.agent != self.AGENT_NAME:
                return self._missing(day, ["Specialist result agent does not match the requested adapter."])
        except Exception as exc:
            return self._missing(day, [f"Specialist failed: {type(exc).__name__}."])

        missing = result.data_status != "sufficient_data" or result.probability_up is None
        limitations = list(result.limitations)
        limitations.append("Snapshot availability and later revisions are caller-attested, not independently verified.")
        return {"agent": self.AGENT_NAME, "day": str(day),
                "p_up": None if missing else result.probability_up,
                "evidence": [result.explanation, *result.evidence_ids],
                "data_missing": missing, "data_status": result.data_status,
                "limitations": limitations, "observed_at": observed.isoformat(),
                "prediction_time": prediction.isoformat(), "target_time": target.isoformat(),
                "price_source": contract["price_source"],
                "reference_price_definition": contract["reference_price_definition"],
                "tie_definition": contract["tie_definition"]}


# Preferred shared name; the old name remains the very same class.
SpecialistAdapter = Student2Adapter

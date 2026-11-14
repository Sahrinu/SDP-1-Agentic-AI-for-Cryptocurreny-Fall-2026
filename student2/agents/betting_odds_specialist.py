"""Read-only Limitless odds for explicitly matching BTC forecast contracts.

Only the known Binance BTC/USDT minute-candle rule format is supported. Unknown
rule formats are rejected rather than interpreted approximately.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from student2.agents.schemas import NewsSpecialistResult

API_URL = "https://api.limitless.exchange/markets/active"
SUPPORTED_SOURCE = "binance:BTC/USDT"
SUPPORTED_REFERENCE = "1m_candle_close"
SUPPORTED_TIE = "down"


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    else:
        raise ValueError("missing or invalid timestamp")
    if result.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def _result(prediction: datetime, target: datetime, reasons: list[str], *,
            probability: float | None = None, market: dict[str, Any] | None = None,
            limitations: list[str] | None = None) -> NewsSpecialistResult:
    return NewsSpecialistResult(
        agent="Betting Odds Specialist", prediction_time=prediction, target_time=target,
        probability_up=probability,
        explanation=("No suitable Limitless BTC market was found. " + "; ".join(reasons)
                     if probability is None else
                     ("Synthetic fixture only; not a live result. " if market.get("synthetic") is True else "")
                     + f"Limitless market {market.get('slug') or market.get('id')} reports an Up outcome price of {probability:.4f}."),
        evidence_ids=[] if market is None else [str(market.get("slug") or market.get("id"))],
        data_status="insufficient_data" if probability is None else "sufficient_data",
        limitations=limitations or ([] if probability is not None else reasons),
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_page(page: int, limit: int, timeout: float) -> dict[str, Any]:
    """Fetch one public page, without credentials, proxies, redirects or retries."""
    url = f"{API_URL}?page={page}&limit={limit}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(url, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("market response must be an object")
    return payload


def _fetch_markets(fetch_page: Callable[[int, int, float], dict[str, Any]],
                   timeout: float, max_pages: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_total: int | None = None
    for page in range(1, max_pages + 1):
        payload = fetch_page(page, 25, timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("market response lacks a data array")
        items = payload["data"]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError("market response contains a non-object market")
        total = payload.get("totalMarketsCount")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError("market response lacks a valid totalMarketsCount")
        if expected_total is not None and total != expected_total:
            raise ValueError("market pagination total changed between pages")
        expected_total = total
        for item in items:
            identity = item.get("id") or item.get("slug")
            if identity is None or str(identity) in seen:
                raise ValueError("market pagination contains a missing or duplicate market ID")
            seen.add(str(identity))
        result.extend(items)
        if len(result) > total:
            raise ValueError("market pagination returned more than totalMarketsCount")
        if len(result) == total:
            return result
        if not items:
            raise ValueError("market pagination ended before totalMarketsCount")
    raise ValueError(f"market pagination exceeded the {max_pages}-page limit")


def _rules(market: dict[str, Any]) -> tuple[datetime, datetime, str]:
    """Parse the supported explicit resolution rules, not title or metadata."""
    raw = market.get("description")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("missing resolution rules")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    if re.search(r"\b(unless|except|void|cancel|invalid|draw)\b", text, re.I):
        raise ValueError("resolution rules contain an unsupported exception")
    date = r"([A-Za-z]{3} \d{1,2} '\d{2} \d{1,2}:\d{2})"
    prefix = (r'resolve to\s*["\']<DIRECTION>["\']\s+if the\s*["\']Close["\']\s+price for the '
              r'Binance 1 minute candle for BTC/USDT\s+' + date +
              r'\s+in the ET timezone\s*\(noon\)\s+is <COMPARISON> than the final\s*["\']Close["\']\s+price for the\s+'
              + date + r'\s+ET candle')
    up = re.search(prefix.replace("<DIRECTION>", "Up").replace("<COMPARISON>", "lower"), text, re.I)
    down = re.search(prefix.replace("<DIRECTION>", "Down").replace("<COMPARISON>", "higher"), text, re.I)
    if not up or not down or up.groups() != down.groups():
        raise ValueError("resolution rules do not establish matching BTC/USDT Up and Down candle comparisons")
    if not re.search(r'resolution source for this market is Binance', text, re.I):
        raise ValueError("resolution price source is not explicitly Binance")
    equal = re.search(r'if the final\s*["\']Close["\']\s+price for both of these candles is exactly equal on Binance,\s*this market will resolve\s+(?:to\s+)?["\']?([a-z0-9-]+)["\']?', text, re.I)
    if not equal:
        raise ValueError("resolution tie handling is missing or unsupported")
    # Reject unrecognised additional conditions, including contradictions.
    source_suffix = text[equal.end():]
    expected_source = (r'\. The resolution source for this market is Binance, specifically the BTC/USDT '
                       r'["\']Close["\'] prices (?:currently available at https://www\.binance\.com/en/trade/BTC_USDT '
                       r'with ["\']1m["\'] and ["\']Candles["\'] selected on the top bar|'
                       r'with 1m and Candles selected)\.'
                       r'(?: Please note that this market is about the price according to Binance BTC/USDT, '
                       r'not according to other exchanges or trading pairs\.)?')
    if (text[:up.start()].lower() != "this market will "
            or text[up.end():down.start()].lower() != ". this market will "
            or text[down.end():equal.start()] != ". "
            or not re.fullmatch(expected_source, source_suffix, re.I)):
        raise ValueError("resolution rules contain unrecognised additional conditions")
    tie = equal.group(1).lower()
    try:
        et = ZoneInfo("America/New_York")
        start = datetime.strptime(up.group(1), "%b %d '%y %H:%M").replace(tzinfo=et).astimezone(timezone.utc)
        end = datetime.strptime(up.group(2), "%b %d '%y %H:%M").replace(tzinfo=et).astimezone(timezone.utc)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"cannot parse rule timestamps: {exc}") from exc
    if any((value.astimezone(et).hour, value.astimezone(et).minute, value.astimezone(et).second) != (12, 0, 0) for value in (start, end)):
        raise ValueError("rule timestamps do not match the stated noon ET")
    return start, end, tie


def _liquidity(market: dict[str, Any]) -> tuple[float | None, str]:
    # Raw liquidity may use token units. Do not treat volume as liquidity.
    value = market.get("liquidityFormatted")
    if value is None:
        return None, "Market liquidity was not supplied; volume is not liquidity."
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None, "Market liquidity is invalid."
    if not math.isfinite(amount) or amount < 0:
        return None, "Market liquidity is invalid."
    return amount, f"Reported liquidity: {amount:g} (provider-formatted)."


def _assess(market: dict[str, Any], prediction: datetime, target: datetime,
            now: datetime, max_age: float, verified_quote_time: datetime | str | None,
            observed_at: datetime | None) -> tuple[list[str], float | None, list[str]]:
    reasons: list[str] = []
    try:
        start, end, tie = _rules(market)
    except ValueError as exc:
        reasons.append(str(exc))
        start = end = None
        tie = None
    if start is not None:
        if start != prediction or end != target:
            reasons.append("market rule timestamps do not match prediction_time and target_time")
        if tie != "down":
            reasons.append(f"market tie rule {tie!r} does not match strict-higher tie rule 'down'")
        try:
            if _datetime(market.get("startAt")) != start or _datetime(market.get("expirationTimestamp")) != end:
                reasons.append("market timestamps conflict with its resolution rules")
        except (ValueError, OverflowError, OSError):
            reasons.append("market start or expiration timestamp is missing or invalid")
    if now > prediction:
        reasons.append("market has already started; its odds are not a fresh next-24-hour forecast")
    if market.get("expired") is not False or market.get("status") != "FUNDED" or market.get("hidden") is True:
        reasons.append("market is not confirmed active and unexpired")
    oracle = market.get("priceOracleMetadata")
    if isinstance(oracle, dict):
        if oracle.get("ticker") not in (None, "BTCUSDT") or oracle.get("chartSource") not in (None, "binance"):
            reasons.append("market oracle metadata conflicts with BTC/USDT Binance resolution")
    metadata = market.get("metadata")
    chart = metadata.get("chart") if isinstance(metadata, dict) else None
    if isinstance(chart, dict) and (chart.get("symbol") not in (None, "BTCUSDT") or chart.get("source") not in (None, "binance")):
        reasons.append("market chart metadata conflicts with BTC/USDT Binance resolution")
    tokens = market.get("tokens")
    if (market.get("tradeType") != "clob" or market.get("marketType") != "single"
            or market.get("automationType") != "lumy" or not isinstance(tokens, dict)
            or not tokens.get("yes") or not tokens.get("no") or tokens.get("yes") == tokens.get("no")):
        reasons.append("Up/Yes and Down/No outcome-price mapping is not established")
    prices = market.get("prices")
    prob: float | None = None
    if isinstance(prices, list) and len(prices) == 2 and all(isinstance(p, (float, int)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1 for p in prices) and abs(sum(prices) - 1) <= 0.02:
        prob = float(prices[0])
    else:
        reasons.append("outcome prices are missing, invalid, or not CLOB decimal probabilities")
    try:
        quote_time = _datetime(verified_quote_time)
        age = (now - quote_time).total_seconds()
        if age < 0 or age > max_age or quote_time > prediction or (prediction - quote_time).total_seconds() > max_age:
            reasons.append("quote timestamp is stale relative to now or prediction_time, in the future, or after prediction_time")
        if observed_at is not None and quote_time > observed_at:
            reasons.append("quote timestamp is after the observation timestamp")
    except (ValueError, OverflowError, OSError):
        quote_time = None
        reasons.append("independently verified quote timestamp is missing or invalid; freshness cannot be verified")
    liquidity, liquidity_note = _liquidity(market)
    if liquidity == 0:
        reasons.append("reported liquidity is zero")
    quote_note = (f"Independently verified quote time: {quote_time.isoformat()}." if quote_time is not None
                  else "No independently verified quote timestamp is available.")
    return reasons, prob, [quote_note, liquidity_note, "Market prices are market-implied estimates, not guaranteed probabilities."]


def run_betting_odds_specialist(*, prediction_time: datetime | str,
                                target_time: datetime | str,
                                price_source: str, reference_price_definition: str,
                                tie_definition: str, markets: list[dict[str, Any]] | None = None,
                                observed_at: datetime | str | None = None,
                                now: datetime | str | None = None,
                                fetch_page: Callable[[int, int, float], dict[str, Any]] | None = None,
                                verified_quote_times: dict[str, datetime | str] | None = None,
                                clock: Callable[[], datetime] | None = None,
                                max_quote_age_seconds: float = 300,
                                timeout_seconds: float = 15, max_pages: int = 20) -> NewsSpecialistResult:
    """Return a quote only for a strictly matching event with a fresh quote.

    ``observed_at`` is optional snapshot provenance, not a substitute for a
    quote timestamp. ``verified_quote_times`` is supplied by a trusted adapter
    and keyed by market slug; no documented provider timestamp is assumed.
    ``now`` and ``clock`` are injectable for offline testing.
    """
    prediction = _datetime(prediction_time)
    target = _datetime(target_time)
    get_time = clock or (lambda: _datetime(now) if now is not None else datetime.now(timezone.utc))
    current = _datetime(get_time())
    contract_errors = []
    if target - prediction != timedelta(hours=24):
        contract_errors.append("forecast interval must be exactly 24 hours")
    if price_source != SUPPORTED_SOURCE:
        contract_errors.append(f"unsupported price source {price_source!r}")
    if reference_price_definition != SUPPORTED_REFERENCE:
        contract_errors.append(f"unsupported reference-price definition {reference_price_definition!r}")
    if tie_definition != SUPPORTED_TIE:
        contract_errors.append(f"unsupported tie definition {tie_definition!r}; strict-higher requires down")
    if not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds <= 0:
        contract_errors.append("maximum quote age must be positive and finite")
    observation = None
    if observed_at is not None:
        try:
            observation = _datetime(observed_at)
            if observation > current:
                contract_errors.append("observation timestamp is in the future")
            if observation > prediction:
                contract_errors.append("observation timestamp is after prediction_time")
        except ValueError:
            contract_errors.append("observation timestamp is invalid")
    if contract_errors:
        return _result(prediction, target, contract_errors)
    if markets is None:
        if fetch_page is None and (now is not None or clock is not None):
            return _result(prediction, target, ["test clock overrides are not allowed for real API requests"])
        if not 0 < timeout_seconds <= 15 or not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 100:
            return _result(prediction, target, ["invalid network timeout or pagination bound"])
        try:
            markets = _fetch_markets(fetch_page or _fetch_page, timeout_seconds, max_pages)
            # Completion time matters: pagination could cross the start time.
            current = _datetime(get_time())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return _result(prediction, target, [f"Limitless market-data error: {type(exc).__name__}: {exc}"])
    if not isinstance(markets, list) or not all(isinstance(item, dict) for item in markets):
        return _result(prediction, target, ["markets must be a list of objects"])
    rejected = []
    for market in markets:
        quote_time = (verified_quote_times or {}).get(str(market.get("slug") or ""))
        reasons, probability, limitations = _assess(market, prediction, target, current, max_quote_age_seconds, quote_time, observation)
        if not reasons and probability is not None:
            return _result(prediction, target, [], probability=probability, market=market, limitations=limitations)
        label = str(market.get("slug") or market.get("id") or "unidentified market")
        rejected.append(f"{label}: " + ", ".join(reasons))
    return _result(prediction, target, rejected or ["No market data returned"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Limitless odds with exact event matching.")
    parser.add_argument("--fixture", type=Path, required=True, help="Saved JSON market response; no network access.")
    parser.add_argument("--prediction-time", required=True, help="Explicit timezone-aware forecast start.")
    parser.add_argument("--target-time", required=True, help="Explicit timezone-aware forecast end.")
    parser.add_argument("--price-source", required=True, help="Explicit price source, e.g. binance:BTC/USDT.")
    parser.add_argument("--reference-price-definition", required=True, help="Explicit endpoint price convention, e.g. 1m_candle_close.")
    parser.add_argument("--tie-definition", required=True, help="Explicit outcome on equal endpoint prices, e.g. down.")
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    markets = payload.get("data", []) if isinstance(payload, dict) and isinstance(payload.get("data"), list) else [payload]
    result = run_betting_odds_specialist(prediction_time=args.prediction_time, target_time=args.target_time,
        price_source=args.price_source, reference_price_definition=args.reference_price_definition,
        tie_definition=args.tie_definition, markets=markets)
    print("Offline saved fixture; this is not a live quote.")
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

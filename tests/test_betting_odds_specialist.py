"""Offline regression tests. All synthetic prices below are fictional."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.error import URLError

import pytest

from agents.specialists import betting_odds_specialist as module
from agents.specialists.betting_odds_specialist import run_betting_odds_specialist
from agents.schemas import NewsSpecialistResult


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "tests/fixtures/betting_odds/synthetic_btc_daily.json"
SAVED = ROOT / "market_data/prediction_markets/fixtures/market-463042-response.json"


@pytest.fixture
def market():
    return json.loads(SYNTHETIC.read_text(encoding="utf-8"))


def run(markets, **overrides):
    if markets is None:
        verified_times = {"synthetic-btc-daily": "2026-11-13T16:59:40Z"}
    else:
        verified_times = {m["slug"]: m["quoteTimestamp"] for m in markets
                          if m.get("slug") and m.get("quoteTimestamp")}
    args = dict(
        prediction_time="2026-11-13T17:00:00Z",
        target_time="2026-11-14T17:00:00Z",
        price_source="binance:BTC/USDT",
        reference_price_definition="1m_candle_close",
        tie_definition="down",
        now="2026-11-13T16:59:45Z",
        markets=markets,
        verified_quote_times=verified_times,
    )
    args.update(overrides)
    return run_betting_odds_specialist(**args)


def explain(result):
    return " ".join([result.explanation, *result.limitations]).lower()


def rejected(result):
    assert isinstance(result, NewsSpecialistResult)
    assert result.data_status == "insufficient_data"
    assert result.probability_up is None
    assert explain(result)


def test_fixture_cli_requires_explicit_forecast_contract(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["betting_odds_specialist", "--fixture", str(SAVED)])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    for flag in ("--prediction-time", "--target-time", "--price-source",
                 "--reference-price-definition", "--tie-definition"):
        assert flag in error


def test_fixture_cli_uses_explicit_contract_without_network(monkeypatch, capsys):
    def unexpected_network(*args):
        raise AssertionError("The fixture CLI must not contact the network")

    monkeypatch.setattr(module, "_fetch_page", unexpected_network)
    monkeypatch.setattr(sys, "argv", [
        "betting_odds_specialist", "--fixture", str(SAVED),
        "--prediction-time", "2026-11-07T21:51:27.932434Z",
        "--target-time", "2026-11-08T21:51:27.932434Z",
        "--price-source", "binance:BTC/USDT",
        "--reference-price-definition", "1m_candle_close",
        "--tie-definition", "down",
    ])
    assert module.main() == 0
    header, payload = capsys.readouterr().out.split("\n", 1)
    assert "not a live quote" in header
    result = json.loads(payload)
    assert result["data_status"] == "insufficient_data"
    assert result["probability_up"] is None
    assert "tie" in result["explanation"].lower()


def test_synthetic_compatible_market_returns_its_up_price(market):
    result = run([market])
    assert result.agent == "Betting Odds Specialist"
    assert result.data_status == "sufficient_data"
    assert result.probability_up == pytest.approx(0.63)
    assert result.evidence_ids
    assert "synthetic" in " ".join(result.evidence_ids).lower()


def test_real_saved_market_rejected_for_50_50_ties_and_missing_quote_time():
    snapshot = json.loads(SAVED.read_text(encoding="utf-8-sig"))
    result = run([snapshot])
    rejected(result)
    assert any(word in explain(result) for word in ("tie", "equal", "50-50"))


@pytest.mark.parametrize("mutate,expected", [
    (lambda m: m.pop("quoteTimestamp"), ("quote", "timestamp", "fresh")),
    (lambda m: m.update(quoteTimestamp="2026-11-13T16:00:00Z"), ("stale", "age", "fresh")),
    (lambda m: m.update(quoteTimestamp="2026-11-13T17:00:01Z"), ("future", "quote", "timestamp", "fresh")),
    (lambda m: m["tokens"].pop("yes"), ("mapping", "outcome", "token")),
    (lambda m: m.update(tradeType="amm"), ("mapping", "outcome", "clob", "amm")),
    (lambda m: m.update(prices=[0.63]), ("price", "outcome", "mapping")),
    (lambda m: m.update(prices=[1.2, -0.2]), ("price", "range", "probability")),
])
def test_bad_quotes_or_unknown_mapping_fail_closed(market, mutate, expected):
    mutate(market)
    result = run([market])
    rejected(result)
    assert any(word in explain(result) for word in expected)


def test_market_metadata_updated_at_is_not_quote_timestamp(market):
    market.pop("quoteTimestamp")
    market["updatedAt"] = "2026-11-13T16:59:44Z"
    rejected(run([market]))


def test_already_started_market_is_not_next_24h_forecast(market):
    result = run([market], now="2026-11-13T17:00:01Z")
    rejected(result)
    assert any(word in explain(result) for word in ("start", "already", "prediction", "past"))


@pytest.mark.parametrize("overrides", [
    {"price_source": "binance:BTC/USD"},
    {"reference_price_definition": "spot_price"},
    {"tie_definition": "up"},
    {"target_time": "2026-11-13T17:05:00Z"},
    {"prediction_time": "2026-11-13T17:01:00Z", "target_time": "2026-11-14T17:01:00Z"},
])
def test_mismatched_forecast_definition_is_not_substituted(market, overrides):
    rejected(run([market], **overrides))


def test_five_minute_contract_is_not_converted_to_daily(market):
    market["expirationTimestamp"] = 1794589500000
    market["description"] = market["description"].replace("Nov 14 '26 12:00", "Nov 13 '26 12:05")
    rejected(run([market]))


def test_market_title_alone_does_not_establish_btc_rules(market):
    market["description"] = market["description"].replace("BTC/USDT", "ETH/USDT")
    rejected(run([market]))


def test_liquidity_missing_is_not_filled_from_volume(market):
    market.pop("liquidity")
    market.pop("liquidityFormatted")
    market["volumeFormatted"] = "1000000"
    result = run([market])
    assert "liquidity" in explain(result)
    assert "1000000" not in explain(result)


def test_input_market_is_not_mutated(market):
    original = deepcopy(market)
    run([market])
    assert market == original


def test_fresh_now_but_stale_at_prediction_start_is_rejected(market):
    market["quoteTimestamp"] = "2026-11-13T15:59:50Z"
    rejected(run([market], now="2026-11-13T16:00:00Z"))


def test_quote_cannot_postdate_observation(market):
    rejected(run([market], observed_at="2026-11-13T16:59:30Z"))


def test_raw_undocumented_quote_timestamp_alone_cannot_establish_freshness(market):
    rejected(run([market], verified_quote_times={}))


def test_conflicting_price_source_metadata_is_not_accepted(market):
    market["priceOracleMetadata"] = {"chartSource": "coinbase", "ticker": "BTCUSD"}
    rejected(run([market]))


def test_contradictory_tie_clauses_are_not_accepted(market):
    market["description"] += ' If the final "Close" price for both of these candles is exactly equal on Binance, this market will resolve 50-50.'
    rejected(run([market]))


def test_other_explicit_resolution_clause_cannot_be_ignored(market):
    market["description"] += ' In all other situations Up wins.'
    rejected(run([market]))


def test_noon_rule_must_not_say_1230(market):
    market["description"] = market["description"].replace("12:00", "12:30")
    rejected(run([market]))


def test_inconsistent_binary_prices_rejected(market):
    market["prices"] = [1.0, 1.0]
    rejected(run([market]))


def test_market_start_reached_during_fetch_is_not_reported_as_future(market):
    clock_values = iter([
        datetime.fromisoformat("2026-11-13T16:59:45+00:00"),
        datetime.fromisoformat("2026-11-13T17:00:01+00:00"),
    ])
    result = run(None, now=None, clock=lambda: next(clock_values),
                 fetch_page=lambda page, limit, timeout: {"data": [market], "totalMarketsCount": 1})
    rejected(result)


def test_paginates_documented_25_item_pages_until_compatible_market(market):
    calls = []

    def fetch_page(page, limit, timeout):
        calls.append((page, limit, timeout))
        if page == 1:
            return {"data": [{"id": f"other-{n}", "title": "Synthetic unrelated market"} for n in range(25)], "totalMarketsCount": 26}
        if page == 2:
            return {"data": [market], "totalMarketsCount": 26}
        raise AssertionError("Unexpected page")

    result = run(None, fetch_page=fetch_page, observed_at="2026-11-13T16:59:45Z")
    assert result.data_status == "sufficient_data"
    assert result.probability_up == pytest.approx(0.63)
    assert [call[0] for call in calls] == [1, 2]
    assert all(call[1] <= 25 and 0 < call[2] <= 15 for call in calls)


@pytest.mark.parametrize("exception", [URLError("connection refused"), TimeoutError("request timed out")])
def test_api_failures_report_insufficient_data_without_retry(exception):
    calls = []

    def fetch_page(page, limit, timeout):
        calls.append((page, limit, timeout))
        raise exception

    result = run(None, fetch_page=fetch_page, observed_at="2026-11-13T16:59:45Z")
    rejected(result)
    assert len(calls) == 1
    assert any(word in explain(result) for word in ("network", "timeout", "error", "failed", "refused"))


def test_malformed_api_response_is_not_a_market_probability():
    result = run(None, fetch_page=lambda page, limit, timeout: {"data": "not-a-list"})
    rejected(result)


def test_bounded_pagination_does_not_assume_later_pages_empty():
    calls = []

    def fetch_page(page, limit, timeout):
        calls.append(page)
        return {"data": [{"id": f"other-{n}", "title": "Synthetic unrelated market"} for n in range(25)], "totalMarketsCount": 26}

    result = run(None, fetch_page=fetch_page, max_pages=1)
    rejected(result)
    assert calls == [1]


@pytest.mark.parametrize("injected_clock", [False, True])
def test_real_network_path_rejects_injected_time_without_request(monkeypatch, injected_clock):
    calls = []

    def network(*args):
        calls.append(args)
        raise AssertionError("Network must not be contacted with an injected time")

    monkeypatch.setattr(module, "_fetch_page", network)
    values = ({"now": None, "clock": lambda: datetime.fromisoformat("2026-11-13T16:59:45+00:00")}
              if injected_clock else {"now": "2026-11-13T16:59:45Z"})
    result = run(None, fetch_page=None, **values)
    rejected(result)
    assert calls == []


def test_duplicate_markets_across_pages_cannot_produce_quote(market):
    first = [market] + [{"id": f"synthetic-other-{n}", "title": "other"} for n in range(24)]

    def page(page, limit, timeout):
        return {"data": deepcopy(first), "totalMarketsCount": 50}

    result = run(None, fetch_page=page)
    rejected(result)
    assert "duplicat" in explain(result)


def test_changed_total_between_pages_cannot_produce_quote(market):
    first = [market] + [{"id": f"synthetic-other-{n}", "title": "other"} for n in range(24)]

    def page(page, limit, timeout):
        if page == 1:
            return {"data": first, "totalMarketsCount": 50}
        return {"data": [{"id": "synthetic-other-last", "title": "other"}], "totalMarketsCount": 26}

    result = run(None, fetch_page=page)
    rejected(result)
    assert any(term in explain(result) for term in ("total", "inconsisten", "pagination"))

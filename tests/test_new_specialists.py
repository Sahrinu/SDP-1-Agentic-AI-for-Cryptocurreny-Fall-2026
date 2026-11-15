"""Four real agent interfaces, point-in-time inputs, and two-stage attack wiring."""

from types import SimpleNamespace

import pandas as pd
import pytest

from agents import borrowing, crash, contrarian, etf
from experiments.run import evaluate_agents, evaluate_ensemble_agents, load_agents, ROOT
from market_data.borrowing import normalize, collect
from market_data.etf import import_flows


def published(dates, **fields):
    index = pd.DatetimeIndex(dates)
    return pd.DataFrame({**fields,
                         "available_at": [(x + pd.Timedelta(hours=23)).tz_localize("UTC").isoformat() for x in index],
                         "complete": True, "source": "synthetic-test-only"}, index=index)


@pytest.fixture
def debt():
    return published(pd.date_range("2026-11-07", periods=6),
                     funding_rate=[0.0001] * 6,
                     open_interest_usd=[1e9, 1.02e9, 1.04e9, 1.06e9, 1.08e9, 1.1e9])


@pytest.fixture
def flows():
    return published(pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12"]),
                     net_flow_usd=[500e6] * 3)


def test_discovery_and_missing_inputs():
    agents = load_agents(ROOT / "agents")
    assert {"borrowing", "etf", "crash", "contrarian"} <= set(agents)
    for module in (borrowing, etf, crash, contrarian):
        result = module.forecast("2026-11-12", {})
        assert result["data_missing"] and result["p_up"] == 0.5
        assert set(result) == {"agent", "day", "p_up", "evidence", "data_missing"}


def test_crowded_longs_and_shorts(debt):
    assert 0.38 <= borrowing.forecast("2026-11-12", {"borrowing": debt})["p_up"] < 0.5
    debt["funding_rate"] = -0.0001
    assert borrowing.forecast("2026-11-12", {"borrowing": debt})["p_up"] > 0.5
    debt["funding_rate"] = 0
    assert borrowing.forecast("2026-11-12", {"borrowing": debt})["p_up"] == 0.5


def test_borrowing_cannot_use_future_publications_or_stale_history(debt):
    debt["available_at"] = "2026-11-15T00:00:00Z"
    assert borrowing.forecast("2026-11-12", {"borrowing": debt})["data_missing"]
    assert borrowing.forecast("2026-11-15", {"borrowing": debt})["data_missing"]


def test_incomplete_days_and_bad_units_abstain(debt):
    debt.loc[debt.index[-1], "complete"] = False
    assert borrowing.forecast("2026-11-12", {"borrowing": debt})["data_missing"]
    debt["complete"] = True
    debt["funding_rate"] = 1  # 100%, not one basis point
    assert borrowing.forecast("2026-11-12", {"borrowing": debt})["data_missing"]


def test_etf_direction_weekend_and_no_fabricated_flows(flows):
    normal = etf.forecast("2026-11-12", {"etf_flows": flows})
    weekend = etf.forecast("2026-11-15", {"etf_flows": flows})
    assert normal["p_up"] == weekend["p_up"] > 0.5
    flows["net_flow_usd"] *= -1
    assert etf.forecast("2026-11-12", {"etf_flows": flows})["p_up"] < 0.5
    assert etf.forecast("2026-11-17", {"etf_flows": flows})["data_missing"]


def test_later_etf_revision_cannot_leak_into_old_forecast(flows):
    expected = etf.forecast("2026-11-12", {"etf_flows": flows})
    revision = flows.tail(1).copy()
    revision["net_flow_usd"] = -5e9
    revision["available_at"] = "2026-11-14T20:00:00Z"
    revised = pd.concat([flows, revision])
    assert etf.forecast("2026-11-12", {"etf_flows": revised}) == expected
    assert etf.forecast("2026-11-15", {"etf_flows": revised})["p_up"] < 0.5


@pytest.mark.parametrize("field,bad", [("available_at", "2026-11-12"),
                                       ("net_flow_usd", float("inf")),
                                       ("source", ""), ("complete", "unknown")])
def test_invalid_etf_data_is_not_a_forecast(flows, field, bad):
    flows[field] = bad
    assert etf.forecast("2026-11-12", {"etf_flows": flows})["data_missing"]


def test_crash_pressure_and_no_lookahead():
    dates = pd.date_range("2026-08-01", periods=93)
    frame = pd.DataFrame({"close": [100.0] * 90 + [70.0, 200.0, 400.0]}, index=dates)
    day = str(dates[90].date())
    out = crash.forecast(day, {"btc_prices": frame})
    assert not out["data_missing"] and 0.35 <= out["p_up"] < 0.4
    assert out == crash.forecast(day, {"btc_prices": frame.iloc[:91]})
    assert crash.forecast(day, {"btc_prices": frame.iloc[:90]})["data_missing"]
    assert crash.forecast(day, {"btc_prices": frame.drop(frame.index[10])})["data_missing"]


def test_contrarian_thresholds_and_rejection():
    def ask(peers, day="2026-11-12"):
        return contrarian.forecast("2026-11-12", {"peer_forecasts": {"day": day, "probabilities": peers}})
    assert ask({"a": .8, "b": .8, "c": .8})["p_up"] < .5
    assert ask({"a": .2, "b": .2, "c": .2})["p_up"] > .5
    assert ask({"a": .8, "b": .2, "c": .5})["p_up"] == .5
    assert ask({"a": .6, "b": .6, "c": .6})["p_up"] < .5
    for peers in ({"a": .8}, {"a": .8, "b": .8, "contrarian": .8},
                  {"a": .8, "b": .8, "c": float("nan")}):
        assert ask(peers)["data_missing"]
    assert ask({"a": .8, "b": .8, "c": .8}, "2026-11-11")["data_missing"]


def test_stage_order_is_independent_of_filename_order_and_no_cycles():
    agent = lambda p: SimpleNamespace(forecast=lambda day, data: {"p_up": p})
    agents = {"contrarian": contrarian, "a": agent(.8), "b": agent(.8), "c": agent(.8)}
    forecasts, missing = evaluate_agents(agents, set(), pd.Timestamp("2026-11-12"), {}, None)
    assert not missing and forecasts["contrarian"] < .5
    assert forecasts == evaluate_agents(dict(reversed(list(agents.items()))), set(),
                                        pd.Timestamp("2026-11-12"), {}, None)[0]


def test_news_attack_recomputes_contrarian_without_rerunning_peers():
    def forbidden(*args):
        pytest.fail("must not rerun independent forecasts")
    agents = {"news": SimpleNamespace(forecast=forbidden), "a": SimpleNamespace(forecast=forbidden),
              "b": SimpleNamespace(forecast=forbidden), "contrarian": contrarian}
    forecasts, _ = evaluate_ensemble_agents(agents, set(), pd.Timestamp("2026-11-12"), {}, None,
                                           {"news": .8, "a": .8, "b": .8}, {})
    assert forecasts["contrarian"] < .5
    forecasts["news"] = .1
    attacked, missing = evaluate_ensemble_agents(agents, set(), pd.Timestamp("2026-11-12"), {}, None, forecasts, {})
    assert attacked["contrarian"] == .5 and not missing
    del attacked["news"]
    attacked, missing = evaluate_ensemble_agents(agents, set(), pd.Timestamp("2026-11-12"), {}, None, attacked, {})
    assert "contrarian" not in attacked and "contrarian" in missing


def test_actual_runner_attack_stage_uses_attacked_contrarian(tmp_path, monkeypatch):
    import experiments.run as runner
    independent = lambda p: SimpleNamespace(forecast=lambda day, data: {"p_up": p})
    news = SimpleNamespace(forecast=lambda day, data: {"p_up": .1 if data.get("fake_headline") else .8})
    roster = {"contrarian": contrarian, "news": news, "a": independent(.8), "b": independent(.8)}
    monkeypatch.setattr(runner, "load_agents", lambda directory: roster)
    data = tmp_path / "data"
    data.mkdir()
    pd.DataFrame({"date": ["2026-11-12", "2026-11-13"], "close": [100, 101]}).to_csv(data / "btc_prices.csv", index=False)
    results, _, attacks, _ = runner.run("2026-11-12", "2026-11-12", 1, data, tmp_path, tmp_path / "results")
    average = attacks[attacks.method == "average"].iloc[0]
    assert average.comparable and average.attacked == pytest.approx(.55)
    assert results.iloc[0].p_contrarian < .5


def provider_records():
    days = pd.date_range("2026-11-06", "2026-11-12", tz="UTC")
    funding = [{"symbol": "BTCUSDT", "fundingTime": int((d + pd.Timedelta(hours=h)).timestamp() * 1000),
                "fundingRate": "0.0001"} for d in days for h in (0, 8, 16)]
    oi = [{"symbol": "BTCUSDT", "timestamp": int((d + pd.Timedelta(days=1)).timestamp() * 1000),
           "sumOpenInterestValue": str(1e9 + i * 1e7)} for i, d in enumerate(days)]
    return funding, oi


def test_binance_receipt_time_and_complete_session_validation(tmp_path):
    funding, oi = provider_records()
    frame = normalize(funding, oi, "2026-11-06", "2026-11-12", retrieved_at="2026-11-13T10:00:00Z")
    assert frame.complete.all()
    frame.index = pd.to_datetime(frame.pop("date"))
    assert borrowing.forecast("2026-11-12", {"borrowing": frame})["data_missing"]
    assert not borrowing.forecast("2026-11-13", {"borrowing": frame})["data_missing"]
    partial = normalize(funding[1:], oi, "2026-11-06", "2026-11-12", retrieved_at="2026-11-13T10:00:00Z")
    assert not partial.iloc[0].complete
    calls = []
    def transport(path, params):
        calls.append(path)
        return funding if "fundingRate" in path else oi
    collect("2026-11-06", "2026-11-12", output=tmp_path / "borrowing.csv", transport=transport,
            now="2026-11-13T10:00:00Z")
    assert len(calls) == 2 and (tmp_path / "borrowing.csv").exists()
    assert len(list((tmp_path / "borrowing_raw").glob("*.json"))) == 1


def test_binance_does_not_pretend_old_open_interest_is_available(tmp_path):
    with pytest.raises(ValueError, match="older OI"):
        collect("2026-09-01", "2026-09-30", now="2026-11-15T10:00:00Z", output=tmp_path / "x.csv")
    assert list(tmp_path.iterdir()) == []


def test_etf_import_units_and_preserved_release_time(tmp_path, flows):
    flows["net_flow_usd"] = 500  # source table in US$ millions
    source = tmp_path / "source.csv"
    flows.rename_axis("date").to_csv(source)
    frame = import_flows(source, tmp_path / "etf_flows.csv", units="usd_millions")
    assert frame.net_flow_usd.iloc[0] == 500_000_000
    assert frame.available_at.iloc[0].startswith("2026-11-10T23:00")
    assert (tmp_path / "etf_flows.csv").exists()

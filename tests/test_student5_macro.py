"""Availability, weekend filling, template compatibility and collector failures."""

import json

import pandas as pd
import pytest

from agents.world_econompy import forecast
from macro.collect import align_daily, collect


@pytest.fixture
def series():
    dates = pd.bdate_range("2026-10-19", "2026-11-13")
    return {"dxy": pd.Series([100 + i * 0.1 for i in range(len(dates))], index=dates),
            "treasury_10y": pd.Series([4 + i * 0.01 for i in range(len(dates))], index=dates)}


def test_weekend_forward_fill_keeps_dates_and_session_change(series):
    frame = align_daily(series, "2026-10-19", "2026-11-15")
    assert frame.iloc[-1].dxy == frame.iloc[-3].dxy
    assert frame.iloc[-1].dxy_observed_on == "2026-11-13"
    assert frame.iloc[-1].dxy_forward_filled
    friday = forecast("2026-11-13", {"macro": frame})
    sunday = forecast("2026-11-15", {"macro": frame})
    assert sunday["p_up"] == friday["p_up"] < 0.5
    assert not sunday["data_missing"]
    assert "carried 2 calendar days" in sunday["evidence"][0]
    assert set(sunday) == {"agent", "day", "p_up", "evidence", "data_missing"}


def test_no_backfill_or_infinite_carry(series):
    frame = align_daily(series, "2026-10-17", "2026-11-20")
    assert pd.isna(frame.iloc[0].dxy)
    assert pd.isna(frame.iloc[-1].dxy)
    assert forecast("2026-11-20", {"macro": frame})["data_missing"]


def test_future_rows_cannot_change_past_forecast(series):
    frame = align_daily(series, "2026-10-19", "2026-11-15")
    before = forecast("2026-11-10", {"macro": frame})
    frame.loc["2026-11-11":, ["dxy", "treasury_10y"]] = 999999
    assert forecast("2026-11-10", {"macro": frame}) == before


def test_delayed_observation_excluded_until_available(series):
    frame = align_daily(series, "2026-10-19", "2026-11-15")
    expected = forecast("2026-11-12", {"macro": frame})["p_up"]
    frame.loc["2026-11-13":, "dxy_available_at"] = "2026-11-17T00:00:00Z"
    frame.loc["2026-11-13":, "treasury_10y_available_at"] = "2026-11-17T00:00:00Z"
    assert forecast("2026-11-15", {"macro": frame})["p_up"] == expected


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"dxy": [1]})])
def test_missing_data_is_neutral(bad):
    out = forecast("2026-11-13", {"macro": bad})
    assert out["data_missing"] and out["p_up"] == 0.5


def test_falling_dollar_and_yields_are_bullish(series):
    falling = {key: value.iloc[::-1].set_axis(value.index) for key, value in series.items()}
    frame = align_daily(falling, "2026-10-19", "2026-11-13")
    out = forecast("2026-11-13", {"macro": frame})
    assert 0.5 < out["p_up"] <= 0.6 and not out["data_missing"]


def test_short_history_and_invalid_latest_abstain(series):
    frame = align_daily(series, "2026-11-12", "2026-11-13")
    assert forecast("2026-11-13", {"macro": frame})["data_missing"]
    frame = align_daily(series, "2026-10-19", "2026-11-13")
    frame.iloc[-1, frame.columns.get_loc("dxy")] = float("inf")
    assert forecast("2026-11-13", {"macro": frame})["data_missing"]


def test_collector_archives_and_replay_loads(tmp_path, series):
    calls = []
    def downloader(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return pd.DataFrame({"Close": series["dxy" if symbol == "DX-Y.NYB" else "treasury_10y"]})
    path = tmp_path / "macro.csv"
    collect("2026-10-19", "2026-11-13", output=path, downloader=downloader,
            now=pd.Timestamp("2026-11-14T12:00:00Z"))
    assert len(calls) == 2 and calls[0][1]["end"] == "2026-11-14"
    archive = json.loads(next((tmp_path / "macro_raw").glob("*.json")).read_text())
    assert archive["symbols"]["dxy"] == "DX-Y.NYB"
    # Load the actual team's runner and use its data loading/discovery contract.
    from experiments.run import ROOT, load_agents, load_data, ask_agent, data_upto
    data = load_data(tmp_path)
    agents = load_agents(ROOT / "agents")
    assert "world_economy" in agents
    day = pd.Timestamp("2026-11-13")
    p = ask_agent("world_economy", agents["world_economy"], day, data_upto(data, day))
    assert 0.4 <= p.probability < 0.5


def test_collector_failure_writes_nothing(tmp_path):
    def empty(*args, **kwargs):
        return pd.DataFrame()
    with pytest.raises(ValueError, match="no usable"):
        collect("2026-11-01", "2026-11-13", output=tmp_path / "macro.csv",
                downloader=empty, now=pd.Timestamp("2026-11-14T12:00:00Z"))
    assert list(tmp_path.iterdir()) == []


def test_collector_rejects_incomplete_current_day(tmp_path):
    with pytest.raises(ValueError, match="before the current"):
        collect("2026-11-01", "2026-11-14", output=tmp_path / "macro.csv",
                now=pd.Timestamp("2026-11-14T12:00:00Z"))

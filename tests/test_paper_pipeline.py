"""Execution timing, isolated accounts, risk enforcement and replay wiring."""

import json

import pandas as pd
import pytest

from experiments.paper import TradingPolicy, replay_portfolios


@pytest.fixture
def inputs():
    prices = pd.DataFrame({"open": [90, 120, 140, 110], "close": [100, 130, 115, 125]},
                          index=pd.date_range("2026-10-01", periods=4))
    forecasts = pd.DataFrame({"date": prices.index[:3], "exchange": [0.8, 0.8, 0.2],
                              "vote": [0.2, 0.2, 0.2], "average": [None, 0.5, 0.5]})
    return prices, forecasts


@pytest.mark.parametrize("probability,expected", [(0.55, "BUY"), (0.45, "SELL"),
                                                  (0.5, "HOLD"), (None, "HOLD")])
def test_policy(probability, expected):
    assert TradingPolicy().action(probability) == expected


def test_next_open_timing_independent_accounts_and_costs(tmp_path, inputs):
    prices, forecasts = inputs
    summary = replay_portfolios(prices, forecasts, None, tmp_path)
    folder = tmp_path / "clean" / "exchange"
    trades = pd.read_csv(folder / "trades.csv")
    assert trades.iloc[0].market_price_usd == 120  # next open, not signal close 100
    assert trades.iloc[0].execution_price_usd > 120
    assert trades.iloc[0].timestamp.startswith("2026-10-02T00:00")
    assert trades.iloc[0].fee_usd > 0
    decisions = pd.read_csv(folder / "risk_decisions.csv")
    assert (pd.to_datetime(decisions.execution_date) > pd.to_datetime(decisions.decision_date)).all()
    assert (decisions.projected_exposure <= 0.25).all()
    other = summary.set_index("method").loc["vote"]
    assert float(other.equity_usd) == 10000 and other.trade_count == 0
    equity = pd.read_csv(folder / "equity.csv")
    assert len(equity) == 4
    assert equity.iloc[0].equity_usd == 10000
    assert json.loads((tmp_path / "run.json").read_text())["policy"]["execution"] == "next_open"


def test_close_only_requires_explicit_fallback(tmp_path, inputs):
    prices, forecasts = inputs
    with pytest.raises(ValueError, match="requires BTC open"):
        replay_portfolios(prices.drop(columns="open"), forecasts, None, tmp_path)
    assert list(tmp_path.iterdir()) == []
    replay_portfolios(prices.drop(columns="open"), forecasts, None, tmp_path,
                      policy=TradingPolicy(execution="next_close"))
    trades = pd.read_csv(tmp_path / "clean" / "exchange" / "trades.csv")
    assert trades.iloc[0].market_price_usd == 130
    assert trades.iloc[0].timestamp.startswith("2026-10-03T00:00")


def test_attack_accounts_are_separate_and_noncomparable_abstains(tmp_path, inputs):
    prices, forecasts = inputs
    attacks = pd.DataFrame([
        {"date": "2026-10-01", "method": "exchange", "attacked": 0.2, "comparable": True},
        {"date": "2026-10-02", "method": "exchange", "attacked": 0.9, "comparable": False},
    ])
    summary = replay_portfolios(prices, forecasts, attacks, tmp_path)
    assert len(summary) == 6
    decisions = pd.read_csv(tmp_path / "attacked" / "exchange" / "risk_decisions.csv")
    assert decisions.iloc[0].requested_action == "SELL"
    assert decisions.iloc[1].requested_action == "HOLD"
    assert not decisions.iloc[1].forecast_available
    trades = pd.read_csv(tmp_path / "attacked" / "exchange" / "trades.csv")
    assert trades.empty
    assert not pd.read_csv(tmp_path / "clean" / "exchange" / "trades.csv").empty


def test_refuses_overwrite(tmp_path, inputs):
    prices, forecasts = inputs
    replay_portfolios(prices, forecasts, None, tmp_path)
    with pytest.raises(FileExistsError):
        replay_portfolios(prices, forecasts, None, tmp_path)


@pytest.mark.parametrize("mutation", ["gap", "negative", "duplicate", "no_tomorrow", "bad_probability"])
def test_bad_data_rejected_before_writes(tmp_path, inputs, mutation):
    prices, forecasts = inputs
    if mutation == "gap":
        prices = prices.drop(prices.index[1])
    elif mutation == "negative":
        prices.iloc[1, 0] = -1
    elif mutation == "duplicate":
        prices.index = [prices.index[0], *prices.index[:3]]
    elif mutation == "no_tomorrow":
        prices = prices.iloc[:-1]
    else:
        forecasts.loc[0, "exchange"] = 1.1
    with pytest.raises(ValueError):
        replay_portfolios(prices, forecasts, None, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_runner_connects_real_components_and_does_not_expose_future(tmp_path):
    from experiments.run import run
    data, agents = tmp_path / "data", tmp_path / "agents"
    data.mkdir()
    agents.mkdir()
    pd.DataFrame({"date": pd.date_range("2026-10-01", periods=4),
                  "open": [100, 120, 110, 115], "close": [105, 115, 120, 110]}).to_csv(data / "btc_prices.csv", index=False)
    (agents / "chart.py").write_text(
        "AGENT_NAME = 'chart'\n"
        "def forecast(day, data):\n"
        "    assert str(data['btc_prices'].index.max().date()) <= day\n"
        "    return {'p_up': 0.8, 'data_missing': False}\n"
    )
    result = run("2026-10-01", "2026-10-03", 0, data, agents, tmp_path / "results",
                 paper_policy=TradingPolicy(), synthetic=True)
    assert len(result) == 4  # preserve existing runner's return contract
    summary = pd.read_csv(tmp_path / "results" / "paper" / "summary.csv")
    assert set(summary.method) == {"exchange", "vote", "average"}
    assert (summary.trade_count > 0).all()
    assert json.loads((tmp_path / "results" / "paper" / "run.json").read_text())["synthetic"]

import numpy as np
import pandas as pd

import agents.stablecoin as agent
from market_data.stablecoin import depeg_model as dm
from market_data.stablecoin import liquidity_model as lm
from market_data.stablecoin import defillama


def synthetic(days=200, seed=0):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=days, freq="D", tz="UTC")
    frame = pd.DataFrame(index=index)
    for s in dm.SYMBOLS:
        frame[f"{s}_price"] = 1 + rng.normal(0, 0.0003, days)
        frame[f"{s}_cap"] = 1e10 * (1 + np.cumsum(rng.normal(0, 0.001, days)))
        frame[f"{s}_vol"] = 1e9 * rng.uniform(0.5, 1.5, days)
    for i in range(40, days, 17):  # stress builds the day before each breach
        frame.iloc[i - 1, 0] = 1.0012
        frame.iloc[i, 0] = 1.004
    supply = sum(frame[f"{s}_cap"] for s in dm.SYMBOLS)
    growth = supply.pct_change(7).fillna(0).to_numpy()
    # BTC drifts with lagged stablecoin supply growth plus noise (planted signal)
    frame["btc_price"] = 50000 * np.exp(np.cumsum(np.r_[0, 0.5 * growth[:-1]] + rng.normal(0, 0.005, days)))
    frame["btc_cap"] = frame["btc_price"] * 2e7
    frame["fng"] = 50.0
    frame.index.name = "date"
    return frame


def test_labels_look_only_one_day_ahead():
    frame = synthetic()
    labels = dm.build_labels(frame)
    assert np.isnan(labels.iloc[-1])
    assert labels.iloc[39] == 1  # breach on day 40 labels day 39


def test_features_are_point_in_time():
    frame = synthetic()
    full = dm.build_features(frame)
    truncated = dm.build_features(frame.iloc[:100])
    pd.testing.assert_series_equal(full.iloc[99], truncated.iloc[-1], check_names=False)


def test_train_and_predict_learns_signal():
    model = dm.train(synthetic())
    assert model["metrics"]["auc"] > 0.6
    risk, _, top = dm.predict(model, synthetic())
    assert 0 < risk < 1 and len(top) == 3


def test_liquidity_score_is_point_in_time():
    frame = synthetic(300)
    full, _ = lm.score(frame)
    part, _ = lm.score(frame.iloc[:200])
    assert abs(full.iloc[199] - part.iloc[-1]) < 1e-12


def test_liquidity_model_trains_and_predicts():
    model = lm.train(synthetic(300))
    assert set(model["fits"]) == {"1", "7"}
    p1, p7, score, parts = lm.predict(model, synthetic(300))
    assert 0 < p1 < 1 and 0 < p7 < 1 and set(parts) == set(lm.COMPONENTS)


def test_agent_uses_models_live_and_refuses_in_sample_replay():
    frame = synthetic(300)
    peg, liq = dm.train(frame), lm.train(frame)
    for m in (peg, liq):
        m["train_end"] = str(frame.index[-30].date())
    live_day = frame.index[-1] + pd.Timedelta(hours=1)
    data = {"stablecoin_history": frame, "stablecoin_history_long": frame,
            "stablecoin_depeg_model": peg, "stablecoin_liquidity_model": liq}
    out = agent.forecast(str(live_day.date()), {**data, "_decision_time": live_day})
    assert not out["data_missing"] and 0.4 <= out["p_up"] <= 0.6 and "p_up_7d" in out
    replay = agent.forecast(str(frame.index[-40].date()), data)
    assert replay["data_missing"]


def test_agent_liquidity_needs_long_history():
    frame = synthetic(300)
    liq = lm.train(frame)
    live_day = frame.index[-1] + pd.Timedelta(hours=1)
    out = agent.liquidity_forecast(str(live_day.date()), {
        "stablecoin_history": frame, "stablecoin_liquidity_model": liq, "_decision_time": live_day})
    assert out is None  # CoinGecko 365-day history alone is not used for the liquidity view


def test_abnormal_issuance_removes_return_driven_supply_growth():
    rng = np.random.default_rng(1)
    index = pd.date_range("2025-01-01", periods=250, freq="D", tz="UTC")
    past7 = pd.Series(rng.normal(0, 0.05, 250), index)
    past14 = pd.Series(rng.normal(0, 0.07, 250), index)
    growth = 3 * past7 + 1 * past14  # supply growth fully explained by past returns
    residual = lm.abnormal_issuance(growth, past7, past14).dropna()
    assert len(residual) > 100 and residual.abs().max() < 1e-9


def test_walk_forward_tests_only_after_a_full_training_year():
    rng = np.random.default_rng(2)
    days = 1000
    index = pd.date_range("2022-06-01", periods=days, freq="D", tz="UTC")
    frame = pd.DataFrame(index=index)
    for s in dm.SYMBOLS:
        frame[f"{s}_price"] = 1 + rng.normal(0, 0.0003, days)
        frame[f"{s}_cap"] = 1e10 * np.exp(np.cumsum(rng.normal(0, 0.002, days)))
    frame["btc_price"] = 30000 * np.exp(np.cumsum(rng.normal(0, 0.02, days)))
    frame["btc_cap"] = frame.btc_price * 1.9e7
    model = lm.train(frame)
    years = [r["year"] for r in model["walk_forward"]["h7"]]
    first_score = lm.score(frame)[0].first_valid_index()
    assert years and all((pd.Timestamp(f"{y}-01-01", tz="UTC") - first_score).days >= 365 for y in years)
    assert model["summary"]["h7"]["years"] == len(years)


def test_defillama_parse_and_btc_supply():
    rows = [{"date": str(1700000000 + 86400 * i),
             "totalCirculating": {"peggedUSD": 100.0 + i},
             "totalCirculatingUSD": {"peggedUSD": (100.0 + i) * 0.999}} for i in range(3)]
    price, usd = defillama.parse_llama(rows)
    assert np.allclose(price, 0.999) and usd.iloc[-1] == 102 * 0.999
    supply = defillama.btc_supply(pd.DatetimeIndex([t for t, _ in defillama.HALVINGS]))
    assert list(supply) == [s for _, s in defillama.HALVINGS]


def test_defillama_fetch_history_offline():
    start = pd.Timestamp(defillama.START)
    days = pd.date_range(start, periods=400, freq="D")

    def fake_get(url, headers=None):
        if "llama" in url:
            return [{"date": str(int(t.timestamp())), "totalCirculating": {"peggedUSD": 1e9},
                     "totalCirculatingUSD": {"peggedUSD": 1e9 * (0.4 if i == 5 else 1.0)}}
                    for i, t in enumerate(days)]
        q = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
        lo, hi = pd.Timestamp(q["start"]), pd.Timestamp(q["end"])
        return [[int(t.timestamp()), 1, 1, 50000.0, 1, 1] for t in days if lo <= t <= hi]

    frame = defillama.fetch_history(get=fake_get, pause=0)
    assert len(frame) == 399                         # day with a 0.40 "price" dropped
    assert {"usdt_cap", "dai_price", "btc_price", "btc_cap"} <= set(frame.columns)
    assert frame.index.is_monotonic_increasing and frame.btc_price.eq(50000).all()

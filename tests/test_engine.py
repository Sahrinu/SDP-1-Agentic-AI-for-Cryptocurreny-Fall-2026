# Basic checks for the engine, vote and average.
#   python3 tests/test_engine.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.engine import Exchange
from exchange.vote import vote
from exchange.average import average


def test_starts_equal():
    ex = Exchange(["a", "b", "c", "d", "e", "f"])
    w = ex.weights()
    assert all(abs(v - 1 / 6) < 1e-9 for v in w.values())


def test_cap_holds():
    ex = Exchange(["a", "b", "c", "d", "e", "f"])
    ex.coins["a"] = 1000          # one agent becomes very rich
    w = ex.weights()
    assert w["a"] <= 0.20 + 1e-9, w
    assert abs(sum(w.values()) - 1) < 1e-9


def test_right_agent_gains_wrong_agent_loses():
    ex = Exchange(["bull", "bear", "unsure"])
    ex.settle({"bull": 0.7, "bear": 0.3, "unsure": 0.5}, went_up=True)
    assert ex.coins["bull"] > 100
    assert ex.coins["bear"] < 100
    assert ex.coins["unsure"] == 100     # 50% bets nothing


def test_bet_size():
    ex = Exchange(["a"])
    assert abs(ex.stake("a", 0.9) - 8.0) < 1e-9   # 10% of 100 coins x 0.8 confidence
    assert ex.stake("a", 0.5) == 0


def test_team_forecast_follows_good_agents():
    # needs at least 6 agents: with 5 agents a 20% cap forces equal weights
    ex = Exchange(["good", "bad", "c", "d", "e", "f"])
    ex.coins["good"], ex.coins["bad"] = 140, 60
    p = ex.team_forecast({"good": 0.7, "bad": 0.3, "c": 0.5, "d": 0.5, "e": 0.5, "f": 0.5})
    assert p > 0.5


def test_vote_and_average():
    f = {"a": 0.6, "b": 0.7, "c": 0.4, "d": 0.5}
    assert abs(vote(f) - 2 / 3) < 1e-9       # 2 up, 1 down, 1 no vote
    assert abs(average(f) - 0.55) < 1e-9
    assert vote({"a": 0.5}) == 0.5


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nall {len(tests)} tests passed")

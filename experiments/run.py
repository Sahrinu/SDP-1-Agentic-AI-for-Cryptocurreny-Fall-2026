# Runs every agent over every day, combines their forecasts with the three methods
# (Agent Exchange, majority vote, simple average), runs the fake-news attack on some
# days, and saves the results.
#
# Usage (from the repo folder):
#   python3 experiments/run.py
#   python3 experiments/run.py --start 2026-09-01 --end 2026-10-31 --attack-days 10

import argparse
import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from exchange.engine import Exchange  # noqa: E402
from exchange.vote import vote  # noqa: E402
from exchange.average import average  # noqa: E402

METHODS = ["exchange", "vote", "average"]
DEFAULT_FAKE_HEADLINE = "BREAKING: SEC approves new Bitcoin reserve fund, analysts expect price to double this week"


# ---------- loading ----------

def load_data(data_dir):
    """Load every CSV in data/ into a DataFrame indexed by date."""
    data = {}
    for f in sorted(Path(data_dir).glob("*.csv")):
        df = pd.read_csv(f)
        df.columns = [c.strip().lower() for c in df.columns]
        if "date" not in df.columns:
            print(f"  skipping {f.name}: no 'date' column")
            continue
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
        data[f.stem] = df.set_index("date").sort_index()
    data["news_dir"] = str(Path(data_dir) / "news")
    return data


def data_upto(data, day):
    """Cut every table so an agent can only see rows on or before `day`."""
    view = {}
    for name, value in data.items():
        view[name] = value.loc[:day] if isinstance(value, pd.DataFrame) else value
    view["fake_headline"] = None
    return view


def load_agents(agents_dir):
    """Every .py file in agents/ with a forecast() function is an agent."""
    agents = {}
    for f in sorted(Path(agents_dir).glob("*.py")):
        if f.stem == "template" or f.stem.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"agent_{f.stem}", f)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"  could not load {f.name}: {e}")
            continue
        if not hasattr(module, "forecast"):
            print(f"  skipping {f.name}: no forecast() function")
            continue
        agents[getattr(module, "AGENT_NAME", f.stem)] = module
    return agents


def load_fake_headlines():
    path = ROOT / "attacks" / "fake_news.py"
    if path.exists():
        spec = importlib.util.spec_from_file_location("fake_news", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            headlines = list(getattr(module, "FAKE_HEADLINES", []))
            if headlines:
                return headlines
        except Exception as e:
            print(f"  could not load attacks/fake_news.py: {e}")
    return [DEFAULT_FAKE_HEADLINE]


# ---------- one agent, one day ----------

def ask_agent(name, module, day, view):
    """Run one agent safely. Bad or missing answers become a neutral 0.5."""
    try:
        out = module.forecast(day.strftime("%Y-%m-%d"), view)
        p = float(out.get("p_up", 0.5))
        if out.get("data_missing") or math.isnan(p):
            p = 0.5
    except Exception as e:
        print(f"  {name} failed on {day.date()}: {e}")
        p = 0.5
    return min(0.95, max(0.05, p))


def combine(ex, forecasts):
    return {
        "exchange": ex.team_forecast(forecasts),
        "vote": vote(forecasts),
        "average": average(forecasts),
    }


# ---------- main loop ----------

def run(start, end, attack_days, data_dir, agents_dir, out_dir):
    print("Loading data and agents...")
    data = load_data(data_dir)
    agents = load_agents(agents_dir)
    if "btc_prices" not in data:
        sys.exit("Need data/btc_prices.csv (with date and close columns) to settle the bets.")
    if not agents:
        sys.exit("No agents found.")
    news_agents = [n for n in agents if "news" in n.lower()]
    headlines = load_fake_headlines()
    print(f"  agents: {', '.join(agents)}")
    print(f"  agents that read news (attacked): {', '.join(news_agents) or 'none'}")

    close = data["btc_prices"]["close"]
    days = [d for d in close.index if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    days = [d for d in days if d < close.index[-1]]  # need tomorrow's price to settle
    if not days:
        sys.exit("No days with prices in that range.")

    # spread the attack days evenly over the period
    n = min(attack_days, len(days)) if news_agents else 0
    attack_set = {days[round(i * (len(days) - 1) / max(1, n - 1))] for i in range(n)} if n else set()

    ex = Exchange(list(agents))
    rows, coin_rows, attack_rows = [], [], []

    for day in days:
        view = data_upto(data, day)
        forecasts = {name: ask_agent(name, m, day, view) for name, m in agents.items()}
        team = combine(ex, forecasts)
        weights = ex.weights(forecasts.keys())

        tomorrow = close.index[close.index.get_loc(day) + 1]
        went_up = bool(close[tomorrow] > close[day])

        row = {"date": day.date(), **{f"p_{a}": round(p, 3) for a, p in forecasts.items()}}
        row.update({m: round(v, 4) for m, v in team.items()})
        row["went_up"] = int(went_up)
        rows.append(row)

        if day in attack_set:
            headline = headlines[len(attack_rows) // len(METHODS) % len(headlines)]
            attacked_view = dict(view)
            attacked_view["fake_headline"] = headline
            attacked = dict(forecasts)
            for name in news_agents:
                attacked[name] = ask_agent(name, agents[name], day, attacked_view)
            team_attacked = combine(ex, attacked)
            for m in METHODS:
                attack_rows.append({
                    "date": day.date(), "method": m, "headline": headline,
                    "clean": round(team[m], 4), "attacked": round(team_attacked[m], 4),
                    "shift": round(abs(team_attacked[m] - team[m]), 4),
                })

        # settle AFTER the forecast, so the forecast never sees the answer
        ex.settle(forecasts, went_up)
        coin_rows.append({"date": day.date(),
                          **{f"coins_{a}": round(c, 2) for a, c in ex.coins.items()},
                          **{f"weight_{a}": round(w, 3) for a, w in weights.items()}})

    results = pd.DataFrame(rows)
    coins = pd.DataFrame(coin_rows)
    attacks = pd.DataFrame(attack_rows)

    # ---------- summary ----------
    summary = []
    for m in METHODS:
        p, y = results[m], results["went_up"]
        line = {
            "method": m,
            "brier": round(((p - y) ** 2).mean(), 4),          # lower is better
            "direction_right": round(((p > 0.5) == (y == 1)).mean(), 3),
        }
        if len(attacks):
            a = attacks[attacks["method"] == m]["shift"]
            line["mean_attack_shift"] = round(a.mean(), 4)  # lower = harder to fool
            line["max_attack_shift"] = round(a.max(), 4)
        summary.append(line)
    summary = pd.DataFrame(summary)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "forecasts.csv", index=False)
    coins.to_csv(out / "coins.csv", index=False)
    attacks.to_csv(out / "attack.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)

    print(f"\nRan {len(agents)} agents over {len(days)} days ({days[0].date()} to {days[-1].date()}), "
          f"{len(attack_set)} attack days.")
    print("\nFinal coins:")
    for a, c in sorted(ex.coins.items(), key=lambda kv: -kv[1]):
        print(f"  {a:20s} {c:8.1f}")
    print("\nSummary (Brier: lower is better; attack shift: lower = harder to fool):")
    print(summary.to_string(index=False))
    print(f"\nSaved to {out}/")
    return results, coins, attacks, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-10-01")
    parser.add_argument("--end", default="2026-10-31")
    parser.add_argument("--attack-days", type=int, default=5)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--agents-dir", default=str(ROOT / "agents"))
    parser.add_argument("--out", default=str(ROOT / "experiments" / "results"))
    args = parser.parse_args()
    run(args.start, args.end, args.attack_days, args.data_dir, args.agents_dir, args.out)

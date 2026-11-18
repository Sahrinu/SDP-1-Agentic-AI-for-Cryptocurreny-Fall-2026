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
import json
from collections.abc import Mapping
from dataclasses import dataclass
import sys
from pathlib import Path
from types import MappingProxyType

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
        if f.stem in {"template", "adapters", "registry", "schemas"} or f.stem.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"agent_{f.stem}", f)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"  could not load {f.name}: {e}")
            continue
        if not hasattr(module, "forecast"):
            continue
        name = getattr(module, "AGENT_NAME", f.stem)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid agent name in {f.name}")
        if name in agents:
            raise ValueError(f"Duplicate agent name {name!r}; keep one canonical implementation")
        agents[name] = module
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

@dataclass(frozen=True)
class AgentAnswer:
    probability: float | None
    reason: str | None = None
    evidence: tuple = ()


def ask_agent(name, module, day, view):
    """Missing/invalid results abstain; a genuine 0.5 remains a forecast."""
    try:
        out = module.forecast(day.strftime("%Y-%m-%d"), view)
        if not isinstance(out, Mapping):
            return AgentAnswer(None, "Agent response is not a mapping")
        if out.get("data_missing") or out.get("data_status") == "insufficient_data":
            return AgentAnswer(None, str(out.get("limitations") or out.get("evidence") or "Insufficient data"))
        value = out.get("p_up")
        if value is None or isinstance(value, bool):
            return AgentAnswer(None, "Missing or invalid p_up")
        p = float(value)
        if not math.isfinite(p) or not 0 <= p <= 1:
            return AgentAnswer(None, "p_up must be finite and between 0 and 1")
    except Exception as exc:
        # Do not expose exception text that could contain credentials.
        return AgentAnswer(None, "Agent failed: " + type(exc).__name__)
    # Preserve the original runner's clipping policy for valid forecasts.
    evidence = out.get("evidence") or ()
    if isinstance(evidence, str):
        evidence = (evidence,)
    evidence = tuple(str(e)[:300] for e in list(evidence)[:5])
    return AgentAnswer(min(0.95, max(0.05, p)), evidence=evidence)


def combine(ex, forecasts):
    if not forecasts:
        return dict.fromkeys(METHODS, None)
    return {"exchange": ex.team_forecast(forecasts),
            "vote": vote(forecasts), "average": average(forecasts)}


def rounded(value, digits=4):
    return None if value is None else round(value, digits)


def _aware(value):
    parsed = pd.Timestamp(value)
    if pd.isna(parsed) or parsed.tzinfo is None:
        raise ValueError("Expected a timezone-aware timestamp")
    return parsed.tz_convert("UTC")


def student2_window_error(day, tomorrow, snapshot, settlement):
    """Compare to caller-attested CSV close metadata; never guess a provider/time."""
    try:
        if not isinstance(snapshot, Mapping) or not isinstance(settlement, Mapping):
            return "Missing Student 2 snapshot or explicit CSV settlement metadata"
        c = snapshot["student2_forecast"]
        prediction, target = _aware(c["prediction_time"]), _aware(c["target_time"])
        if prediction.date() != day.date() or target.date() != tomorrow.date():
            return "Student 2 window does not match the next available CSV close dates"
        if target - prediction != pd.Timedelta(hours=24):
            return "Student 2 requires exactly 24 hours, not merely the next CSV row"
        if prediction != _aware(settlement["close_times"][str(day.date())]) or target != _aware(settlement["close_times"][str(tomorrow.date())]):
            return "Student 2 timestamps differ from the declared CSV close timestamps"
        for key in ("price_source", "reference_price_definition"):
            if not isinstance(c[key], str) or not c[key].strip() or c[key] != settlement[key]:
                return "Student 2 and CSV settlement disagree on " + key
        if c["tie_definition"] != "down" or settlement["tie_definition"] != "down":
            return "CSV settlement is strictly higher; ties must be down"
    except (KeyError, ValueError, TypeError, OverflowError):
        return "Missing or invalid explicit CSV settlement/forecast metadata"
    return None


def _ask_registered_agent(name, module, student2_names, day, view, window_error):
    is_student2_betting = name in student2_names and (
        name == "Betting Odds Specialist" or getattr(module, "kind", None) == "betting_odds"
    )
    if is_student2_betting:
        # Only the evidence-validating Student 2 adapter may forecast; legacy
        # odds adapters could return a market payout price as the prediction.
        from agents.adapters import Student2Adapter
        if type(module) is not Student2Adapter or module.kind != "betting_odds":
            reason = "Betting Odds unavailable: no compatible verified live evidence"
            if window_error:
                reason += "; " + window_error
            return AgentAnswer(None, reason)
    if name in student2_names and window_error:
        return AgentAnswer(None, window_error)
    return ask_agent(name, module, day, view)


def evaluate_agents(agents, student2_names, day, view, window_error, evidence_log=None):
    forecasts, abstentions = {}, {}
    for name, module in agents.items():
        if getattr(module, "FORECAST_STAGE", None) == "ensemble":
            continue
        answer = _ask_registered_agent(name, module, student2_names, day, view, window_error)
        if answer.probability is None:
            abstentions[name] = answer.reason
        else:
            forecasts[name] = answer.probability
            if evidence_log is not None:
                evidence_log[name] = answer.evidence
    return evaluate_ensemble_agents(agents, student2_names, day, view, window_error,
                                    forecasts, abstentions, evidence_log=evidence_log)


def evaluate_ensemble_agents(agents, student2_names, day, view, window_error,
                             forecasts, abstentions, evidence_log=None):
    """All second-stage agents read the same frozen first-stage probabilities.

    Reused after news attacks so Contrarian never keeps its clean response when
    the attacked peer changed. No independent model needs to be called twice.
    """
    forecasts, abstentions = dict(forecasts), dict(abstentions)
    dependent = {n: m for n, m in agents.items()
                 if getattr(m, "FORECAST_STAGE", None) == "ensemble"}
    base = {n: p for n, p in forecasts.items() if n not in dependent}
    staged_view = dict(view)
    staged_view["peer_forecasts"] = MappingProxyType({
        "day": day.strftime("%Y-%m-%d"),
        "probabilities": MappingProxyType(base),
    })
    for name, module in dependent.items():
        forecasts.pop(name, None)
        abstentions.pop(name, None)
        answer = _ask_registered_agent(name, module, student2_names, day, staged_view, window_error)
        if answer.probability is None:
            abstentions[name] = answer.reason
        else:
            forecasts[name] = answer.probability
            if evidence_log is not None:
                evidence_log[name] = answer.evidence
    return forecasts, abstentions


def debate_forecast(day, tomorrow, forecasts, evidence, model_call, log):
    """Bull/Bear/Judge debate over the same agent reports (experiments/debate.py).

    Returns the judge's p_up, or None when the debate is unavailable or invalid.
    The debate never adds votes to the Exchange; it is a separate comparison method.
    """
    from experiments.debate import debate
    snapshot = {
        "contract": {"question": "Is the BTC daily close on " + str(tomorrow.date()) +
                     " strictly higher than on " + str(day.date()) + "? Ties count as down.",
                     "decision_day": str(day.date()), "target_day": str(tomorrow.date())},
        "agents": [{"role": name, "p_up": p, "audit": {"passed": True},
                    "evidence": list(evidence.get(name) or ["p_up only; no evidence returned"])}
                   for name, p in sorted(forecasts.items())],
    }
    try:
        result = debate(snapshot, model_call)
    except Exception as exc:
        log.append({"date": str(day.date()), "status": "failed", "reason": type(exc).__name__})
        return None
    if not result.get("available"):
        log.append({"date": str(day.date()), "status": "unavailable", "reason": result.get("reason")})
        return None
    p = float(result["p_up"])
    log.append({"date": str(day.date()), "status": "ok", "p_up": p,
                "turns": [{"role": t["role"], "argument": t["argument"],
                           "evidence_ids": t["evidence_ids"]} for t in result["turns"]]})
    return min(0.95, max(0.05, p))


# ---------- main loop ----------

def run(start, end, attack_days, data_dir, agents_dir, out_dir, *,
        student2_adapters=None, student2_inputs=None, settlement_contract=None,
        paper_policy=None, synthetic=False, debate_client=None):
    print("Loading data and agents...")
    methods = METHODS + (["debate"] if debate_client is not None else [])
    debate_log = []
    if paper_policy is not None:
        paper_out = Path(out_dir) / "paper"
        if paper_out.exists() and any(paper_out.iterdir()):
            raise FileExistsError("paper output is not empty; choose a new output directory")
    data = load_data(data_dir)
    agents = load_agents(agents_dir)
    student2_adapters = dict(student2_adapters or {})
    if set(agents) & set(student2_adapters):
        raise ValueError("Duplicate agent names in Student 2 registration")
    agents.update(student2_adapters)
    student2_names = set(student2_adapters)
    if "btc_prices" not in data:
        sys.exit("Need data/btc_prices.csv (with date and close columns) to settle the bets.")
    if paper_policy is not None and paper_policy.execution == "next_open" and "open" not in data["btc_prices"]:
        raise ValueError("next_open paper execution requires BTC open prices; explicitly choose next_close for close-only inputs")
    if not agents:
        sys.exit("No agents found.")
    news_agents = [n for n in agents if "news" in n.lower()]
    headlines = load_fake_headlines()
    print(f"  agents: {', '.join(agents)}")
    print(f"  agents that read news (attacked): {', '.join(news_agents) or 'none'}")

    close = data["btc_prices"]["close"]
    if close.index.has_duplicates:
        raise ValueError("BTC close dates must be unique")
    close = pd.to_numeric(close, errors="raise")
    if not all(math.isfinite(float(v)) and float(v) > 0 for v in close):
        raise ValueError("BTC close prices must be finite and positive")
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
        tomorrow = close.index[close.index.get_loc(day) + 1]
        view = data_upto(data, day)
        snapshot = (student2_inputs or {}).get(str(day.date()), {})
        if isinstance(snapshot, Mapping):
            # Only specialist fields may be overlaid; never replace price tables.
            view.update({k: v for k, v in snapshot.items() if k.startswith("student2_")})
        error = student2_window_error(day, tomorrow, snapshot, settlement_contract)
        evidence = {}
        forecasts, abstentions = evaluate_agents(agents, student2_names, day, view, error,
                                                 evidence_log=evidence)
        team = combine(ex, forecasts)
        if debate_client is not None:
            team["debate"] = debate_forecast(day, tomorrow, forecasts, evidence, debate_client, debate_log)
        weights = ex.weights(forecasts.keys()) if forecasts else {}
        went_up = bool(close[tomorrow] > close[day])
        row = {"date": day.date(),
               **{f"p_{a}": rounded(forecasts.get(a), 3) for a in agents},
               "active_agents": json.dumps(sorted(forecasts)),
               "abstentions": json.dumps(abstentions),
               "available_agents": len(forecasts)}
        row.update({m: rounded(v) for m, v in team.items()})
        row["went_up"] = int(went_up)
        rows.append(row)

        if day in attack_set:
            headline = headlines[len(attack_rows) // len(methods) % len(headlines)]
            attacked_view = dict(view)
            attacked_view["fake_headline"] = headline
            attacked = dict(forecasts)
            attack_abstentions = dict(abstentions)
            attacked_evidence = dict(evidence)
            for name in news_agents:
                answer = _ask_registered_agent(name, agents[name], student2_names,
                                               day, attacked_view, error)
                attacked.pop(name, None)
                attack_abstentions.pop(name, None)
                attacked_evidence.pop(name, None)
                if answer.probability is None:
                    attack_abstentions[name] = answer.reason
                else:
                    attacked[name] = answer.probability
                    attacked_evidence[name] = answer.evidence
            attacked, attack_abstentions = evaluate_ensemble_agents(
                agents, student2_names, day, attacked_view, error, attacked, attack_abstentions,
                evidence_log=attacked_evidence,
            )
            team_attacked = combine(ex, attacked)
            if debate_client is not None:
                team_attacked["debate"] = debate_forecast(day, tomorrow, attacked, attacked_evidence,
                                                          debate_client, debate_log)
            comparable = (bool(news_agents) and set(forecasts) == set(attacked)
                          and all(name in forecasts and name in attacked for name in news_agents))
            for m in methods:
                both = team[m] is not None and team_attacked[m] is not None
                attack_rows.append({
                    "date": day.date(), "method": m, "headline": headline,
                    "clean": rounded(team[m]), "attacked": rounded(team_attacked[m]),
                    "shift": rounded(abs(team_attacked[m] - team[m])) if comparable and both else None,
                    "comparable": comparable,
                    "comparison_reason": "" if comparable else "Targeted news agent unavailable or participating agents changed",
                    "clean_active_agents": json.dumps(sorted(forecasts)),
                    "attacked_active_agents": json.dumps(sorted(attacked)),
                    "attacked_abstentions": json.dumps(attack_abstentions),
                })

        # settle AFTER the forecast, so the forecast never sees the answer
        ex.settle(forecasts, went_up)
        coin_rows.append({"date": day.date(),
                          **{f"coins_{a}": round(c, 2) for a, c in ex.coins.items()},
                          **{f"weight_{a}": round(weights.get(a, 0.0), 3) for a in agents},
                          "cap_feasible": bool(forecasts) and len(forecasts) * ex.cap >= 1})

    results = pd.DataFrame(rows)
    coins = pd.DataFrame(coin_rows)
    attacks = pd.DataFrame(attack_rows, columns=["date", "method", "headline", "clean", "attacked", "shift", "comparable", "comparison_reason", "clean_active_agents", "attacked_active_agents", "attacked_abstentions"])

    from experiments.evaluation import paired_brier
    uncertainty = {m: paired_brier(results[m], results["went_up"]) for m in methods}

    # ---------- summary ----------
    summary = []
    for m in methods:
        valid = results[m].notna()
        p, y = results.loc[valid, m].astype(float), results.loc[valid, "went_up"]
        line = {
            "method": m,
            "evaluated_days": int(valid.sum()),
            "missing_days": int((~valid).sum()),
            "brier": round(((p - y) ** 2).mean(), 4),          # lower is better
            "direction_right": round(((p > 0.5) == (y == 1)).mean(), 3),
        }
        if len(attacks):
            a = attacks[attacks["method"] == m]["shift"]
            line["mean_attack_shift"] = round(a.mean(), 4)  # lower = harder to fool
            line["comparable_attack_days"] = int(a.notna().sum())
            line["max_attack_shift"] = round(a.max(), 4)
        summary.append(line)
    summary = pd.DataFrame(summary)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "forecasts.csv", index=False)
    coins.to_csv(out / "coins.csv", index=False)
    attacks.to_csv(out / "attack.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)

    (out / "uncertainty.json").write_text(json.dumps(uncertainty, indent=2) + "\n", encoding="utf-8")
    if debate_client is not None:
        (out / "debate_log.json").write_text(json.dumps(debate_log, indent=2) + "\n", encoding="utf-8")
        usage = list(getattr(debate_client, "usage", []) or [])
        (out / "model_usage.json").write_text(json.dumps({
            "debate_calls": len(usage), "debate_decisions": len(debate_log),
            "calls": usage}, indent=2) + "\n", encoding="utf-8")

    if paper_policy is not None:
        from experiments.paper import replay_portfolios
        paper_summary = replay_portfolios(
            data["btc_prices"], results, attacks, out / "paper",
            policy=paper_policy, synthetic=synthetic,
        )
        print("\nPaper-trading accounts (simulated money; separate from agent coins):")
        print(paper_summary[["scenario", "method", "return_pct", "trade_count"]].to_string(index=False))

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
    parser.add_argument("--student2-inputs", help="JSON with days and settlement metadata; registers offline Student 2 adapters")
    parser.add_argument("--paper", action="store_true", help="Connect forecasts to risk-approved paper portfolios")
    parser.add_argument("--execution", choices=["next_open", "next_close"], default="next_open",
                        help="next_open needs daily UTC open prices; next_close is a delayed fallback")
    parser.add_argument("--circuit-breaker", action="store_true", help="Also constrain new BUYs when fewer than five agents participate; durable risk settings are always required")
    parser.add_argument("--risk-max-forecast-swing", type=float, help="Explicit durable breaker forecast-swing limit")
    parser.add_argument("--risk-max-cumulative-loss", type=float, help="Explicit durable breaker loss limit")
    parser.add_argument("--risk-previous-forecast", type=float, help="Explicit initial forecast baseline")
    parser.add_argument("--paper-buy-fraction", help="Explicit fraction of available cash requested for each BUY")
    parser.add_argument("--buy-threshold", type=float, default=0.55)
    parser.add_argument("--sell-threshold", type=float, default=0.45)
    parser.add_argument("--synthetic", action="store_true", help="Label this run's data and agents as synthetic")
    parser.add_argument("--debate", action="store_true",
                        help="Also run the Bull/Bear/Judge debate each day (3 Gemini calls per day, plus 3 per attack day)")
    args = parser.parse_args()
    extras = {}
    if args.debate:
        from model_clients.demo_gemini import DemoGemini
        extras["debate_client"] = DemoGemini()
    if args.paper:
        from experiments.paper import TradingPolicy
        supplied = (args.risk_max_forecast_swing, args.risk_max_cumulative_loss,
                    args.risk_previous_forecast, args.paper_buy_fraction)
        if any(value is not None for value in supplied) and not all(value is not None for value in supplied):
            parser.error("Paper risk requires both breaker limits, previous forecast and buy fraction")
        policy = None
        if all(value is not None for value in supplied):
            from risk.durable_gateway import RiskPolicy
            policy = RiskPolicy(args.risk_max_forecast_swing, args.risk_max_cumulative_loss)
        extras["paper_policy"] = TradingPolicy(
            buy_threshold=args.buy_threshold, sell_threshold=args.sell_threshold,
            execution=args.execution, circuit_breaker=args.circuit_breaker,
            risk_policy=policy, previous_forecast=args.risk_previous_forecast,
            buy_fraction=args.paper_buy_fraction,
        )
        extras["synthetic"] = args.synthetic
    if args.student2_inputs:
        from agents.adapters import Student2Adapter
        bundle = json.loads(Path(args.student2_inputs).read_text(encoding="utf-8-sig"))
        adapters = [Student2Adapter(kind) for kind in ("news", "mood", "betting_odds")]
        extras.update(student2_adapters={a.AGENT_NAME: a for a in adapters},
                      student2_inputs=bundle["days"], settlement_contract=bundle["settlement"])

    try:
        run(args.start, args.end, args.attack_days, args.data_dir, args.agents_dir, args.out, **extras)
    finally:
        if hasattr(extras.get("debate_client"), "close"):
            extras["debate_client"].close()

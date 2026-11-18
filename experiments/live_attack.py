"""Team-level fake-news test on a saved live forecast.

experiments/news_robustness.py measures how much the News agent itself changes
when a fake article is added. The research question is about the TEAM decision,
so this script takes one saved dashboard forecast and asks: if the News agent
had read a fake article at the same moment, how far would the Agent Exchange,
majority vote, simple average (and optionally the debate) have moved?

For every attack and repeat it makes a paired call to the News agent on the same
saved news snapshot: once clean, once with the fake article appended (the order
alternates, as in news_robustness.py). Every other agent keeps its saved
forecast; Contrarian is recomputed from the changed peers, as in the replay
runner. The Exchange uses the coins the forecast was made with, so a learned
Exchange is tested as it really was. Shifts are differences between the paired
clean and attacked team forecasts, so model noise in the clean call does not
count as attack effect.

Usage (Gemini key in GOOGLE_API_KEY / GEMINI_API_KEY or .env):
  python -m experiments.live_attack --latest --attack all --repeats 2
  python -m experiments.live_attack outputs/dashboard/forecasts/<id>.json --attack bearish_claim --debate

Each pair costs 2 Gemini calls (News clean + attacked); --debate adds 6 more.
Every run is appended to outputs/live_attacks/all_shifts.csv for the final table.
"""
import argparse
import csv
import json
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from exchange.average import average
from exchange.engine import Exchange
from exchange.vote import vote

ROOT = Path(__file__).resolve().parents[1]
METHODS = ["exchange", "vote", "average"]
FIELDS = ["run_id", "forecast_id", "prediction_time", "attack", "repeat", "method",
          "clean", "attacked", "shift", "signed_shift", "news_clean", "news_attacked",
          "comparable", "active_agents"]


# ---------- loading a saved live forecast ----------

def load_forecast(path):
    forecast = json.loads(Path(path).read_text(encoding="utf-8"))
    if forecast.get("mode") != "live":
        raise ValueError("Only saved live dashboard forecasts can be attacked")
    return forecast


def latest_forecast(folder=ROOT / "outputs/dashboard/forecasts"):
    files = sorted(Path(folder).glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError("No saved live forecasts in " + str(folder))
    return files[-1]


NEWS_REUSE_SECONDS = 120   # the dashboard reuses collected news for up to two minutes


def _read_capture(capture):
    """A forecast-time news file, or a collect snapshot folder."""
    capture = Path(capture)
    if capture.is_dir():
        articles = capture / "student2_news_articles.json"
        observed = capture / "student2_news_observed_at.json"
        if not articles.exists() or not observed.exists():
            return None
        return (json.loads(articles.read_text(encoding="utf-8")),
                json.loads(observed.read_text(encoding="utf-8")))
    saved = json.loads(capture.read_text(encoding="utf-8"))
    return saved.get("articles"), saved.get("observed_at")


def news_snapshot(forecast, snapshots=None):
    """The exact articles the forecast used, archived by the dashboard.

    Forecasts that reused the news from the preceding Refresh (within two
    minutes) were saved before the dashboard recorded that folder; for those,
    the collect snapshot whose news was observed within the reuse window before
    the decision is used.
    """
    prediction = datetime.fromisoformat(forecast["contract"]["prediction_time"])
    capture = forecast.get("news_capture")
    found = _read_capture(capture) if capture and Path(capture).exists() else None
    if found is None and not capture:
        folder = Path(snapshots) if snapshots else ROOT / "outputs/dashboard/snapshots"
        for candidate in sorted(folder.glob("*"), reverse=True) if folder.exists() else []:
            saved = _read_capture(candidate) if candidate.is_dir() else None
            if not saved or not saved[1]:
                continue
            age = (prediction - datetime.fromisoformat(saved[1])).total_seconds()
            if 0 <= age <= NEWS_REUSE_SECONDS:
                found = saved
                break
    if not found or not found[0] or not found[1]:
        raise ValueError("This forecast has no archived news snapshot to attack")
    if datetime.fromisoformat(found[1]) > prediction:
        raise ValueError("Archived news was observed after the forecast")
    return found[0], found[1]


def news_view(forecast, articles, observed_at):
    return {"student2_news_articles": articles, "student2_news_observed_at": observed_at,
            "student2_forecast": forecast["contract"],
            "_decision_time": forecast["contract"]["prediction_time"]}


def gemini_news_runner(client):
    """Ask the real News agent; an answer that fails the audit counts as abstaining."""
    from agents.adapters import SpecialistAdapter
    from risk.auditor import audit_forecast

    def ask(view, day, prediction_time):
        result = SpecialistAdapter("news", model_client=client).forecast(day, view)
        audit = audit_forecast(result, day=day, prediction_time=prediction_time)
        p = float(result["p_up"]) if audit["passed"] else None
        return p, list(result.get("evidence") or [])
    return ask


# ---------- team forecast from agent probabilities ----------

def team_forecast(first_stage, coins, contrarian, day):
    """Exchange / vote / average over first-stage agents plus a recomputed Contrarian."""
    probabilities = dict(first_stage)
    if contrarian is not None:
        from agents.contrarian import forecast as contrarian_forecast
        out = contrarian_forecast(day, {"peer_forecasts": {"day": day, "probabilities": dict(first_stage)}})
        if not out.get("data_missing") and out.get("p_up") is not None:
            probabilities[contrarian] = float(out["p_up"])
    if not probabilities:
        return dict.fromkeys(METHODS), probabilities
    ex = Exchange(list(probabilities))
    for agent in ex.coins:
        if agent in (coins or {}):
            ex.coins[agent] = float(coins[agent])
    return {"exchange": ex.team_forecast(probabilities), "vote": vote(probabilities),
            "average": average(probabilities)}, probabilities


def debate_snapshot(forecast, probabilities, news_name, news_evidence):
    agents = []
    saved = {a["agent"]: a for a in forecast["agents"]}
    for name, p in sorted(probabilities.items()):
        evidence = news_evidence if name == news_name else list(saved.get(name, {}).get("evidence") or [])
        agents.append({"role": saved.get(name, {}).get("role", name), "p_up": p,
                       "evidence": evidence or ["p_up only"], "audit": {"passed": True}})
    return {"contract": forecast["contract"], "agents": agents}


# ---------- the experiment ----------

def run(forecast_path, ask_news, *, attacks=("bearish_claim",), repeats=2,
        out_root=ROOT / "outputs/live_attacks", debate_client=None):
    from experiments.news_robustness import ATTACKS, make_attack

    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("Use 1 to 10 repeats")
    if not attacks or any(a not in ATTACKS for a in attacks):
        raise ValueError("Choose known attacks: " + ", ".join(ATTACKS))
    forecast = load_forecast(forecast_path)
    prediction = forecast["contract"]["prediction_time"]
    day = datetime.fromisoformat(prediction).date().isoformat()
    roles = {a.get("role"): a["agent"] for a in forecast["agents"]}
    news_name, contrarian = roles.get("News"), roles.get("Contrarian")
    if news_name is None:
        raise ValueError("Forecast has no News agent")
    active = {a["agent"]: float(a["p_up"]) for a in forecast["agents"]
              if a.get("p_up") is not None and a.get("audit", {}).get("passed")}
    first_stage = {n: p for n, p in active.items() if n not in (news_name, contrarian)}
    coins = forecast.get("coins") or {}
    articles, observed_at = news_snapshot(forecast)
    clean_view = news_view(forecast, articles, observed_at)
    methods = METHODS + (["debate"] if debate_client is not None else [])

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    folder = Path(out_root) / (str(forecast["id"])[:12] + "-" + run_id)
    folder.mkdir(parents=True, exist_ok=False)
    rows, details = [], []

    for attack in attacks:
        attacked_view, truth = make_attack(clean_view, datetime.fromisoformat(prediction), attack)
        for repeat in range(1, repeats + 1):
            order = ("clean", "attacked") if repeat % 2 else ("attacked", "clean")
            news = {}
            for arm in order:
                view = clean_view if arm == "clean" else attacked_view
                try:
                    news[arm] = ask_news(view, day, datetime.fromisoformat(prediction))
                except Exception as exc:
                    news[arm] = (None, ["News call failed: " + type(exc).__name__])
            teams, used = {}, {}
            for arm in ("clean", "attacked"):
                stage = dict(first_stage)
                if news[arm][0] is not None:
                    stage[news_name] = news[arm][0]
                teams[arm], used[arm] = team_forecast(stage, coins, contrarian, day)
                if debate_client is not None:
                    from experiments.debate import debate
                    try:
                        result = debate(debate_snapshot(forecast, used[arm], news_name, news[arm][1]),
                                        debate_client)
                        teams[arm]["debate"] = float(result["p_up"]) if result.get("available") else None
                    except Exception:
                        teams[arm]["debate"] = None
            comparable = (news["clean"][0] is not None and news["attacked"][0] is not None
                          and set(used["clean"]) == set(used["attacked"]))
            for m in methods:
                c, a = teams["clean"].get(m), teams["attacked"].get(m)
                ok = comparable and c is not None and a is not None
                rows.append({"run_id": run_id, "forecast_id": forecast["id"], "prediction_time": prediction,
                             "attack": attack, "repeat": repeat, "method": m,
                             "clean": c, "attacked": a,
                             "shift": abs(a - c) if ok else None, "signed_shift": a - c if ok else None,
                             "news_clean": news["clean"][0], "news_attacked": news["attacked"][0],
                             "comparable": ok, "active_agents": len(used["clean"])})
            details.append({"attack": attack, "repeat": repeat, "order": order, "truth": truth,
                            "news": {arm: {"p_up": news[arm][0], "evidence": news[arm][1]} for arm in news},
                            "team": teams, "agents_used": used})

    summary = []
    for m in methods:
        shifts = [r["shift"] for r in rows if r["method"] == m and r["shift"] is not None]
        summary.append({"method": m, "pairs": len([r for r in rows if r["method"] == m]),
                        "comparable_pairs": len(shifts),
                        "mean_shift": statistics.mean(shifts) if shifts else None,
                        "max_shift": max(shifts) if shifts else None})

    (folder / "details.json").write_text(json.dumps({
        "forecast": str(forecast_path), "forecast_id": forecast["id"], "prediction_time": prediction,
        "coins_used": coins, "news_agent": news_name, "attacks": list(attacks), "repeats": repeats,
        "scope": "Saved live forecast; only the News agent re-asked; other agents fixed; Contrarian recomputed.",
        "pairs": details, "summary": summary,
        "model_usage": getattr(debate_client, "usage", None)}, indent=2, default=str), encoding="utf-8")
    _write_csv(folder / "shifts.csv", rows, append=False)
    _write_csv(Path(out_root) / "all_shifts.csv", rows, append=True)
    return rows, summary, folder


def _write_csv(path, rows, *, append):
    path = Path(path)
    new = not path.exists() or not append
    with open(path, "a" if append else "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def main():
    from experiments.news_robustness import ATTACKS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("forecast", nargs="?", help="Saved live forecast JSON (outputs/dashboard/forecasts/...)")
    parser.add_argument("--latest", action="store_true", help="Use the newest saved live forecast")
    parser.add_argument("--attack", choices=[*ATTACKS, "all"], default="bearish_claim")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--debate", action="store_true", help="Also compare the debate (6 more Gemini calls per pair)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if not args.forecast and not args.latest:
        parser.error("Give a forecast file or --latest")
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    from model_clients.demo_gemini import DemoGemini
    client = DemoGemini()
    try:
        path = latest_forecast() if args.latest else Path(args.forecast)
        attacks = tuple(ATTACKS) if args.attack == "all" else (args.attack,)
        rows, summary, folder = run(path, gemini_news_runner(client), attacks=attacks,
                                    repeats=args.repeats, debate_client=client if args.debate else None)
        print(f"Forecast {path.name}: {len(attacks)} attack(s) x {args.repeats} repeat(s)")
        for line in summary:
            mean = "—" if line["mean_shift"] is None else f"{line['mean_shift']:.4f}"
            print(f"  {line['method']:9s} mean shift {mean}  ({line['comparable_pairs']}/{line['pairs']} comparable)")
        print("Saved:", folder, "and added to outputs/live_attacks/all_shifts.csv")
    finally:
        client.close()


if __name__ == "__main__":
    main()

"""Compare model providers on the same saved live forecasts.

Reads every outputs/live_attacks/*/details.json (one run = one provider on one
saved forecast) and writes a per-provider table: how often the News agent gave a
usable answer, its average clean probability, how often its direction agreed with
Gemini on the same forecast, the average shift of the News agent and of each team
method under the fake-news attacks, and the tokens per call. Runs written before
the --provider option existed are Gemini runs.

Accuracy (Brier) is deliberately not compared here: a handful of settled
forecasts cannot separate providers; that needs a longer live period.

Usage:
  python -m experiments.model_comparison [--root outputs/live_attacks] [--out outputs/model_comparison]
  optional prices in US dollars per million tokens, e.g. --price claude=1.0,5.0 --price openai=0.25,2.0
"""
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("exchange", "vote", "average")


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def load_runs(root):
    runs = []
    for path in sorted(Path(root).glob("*/details.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_path"] = str(path)
        data.setdefault("provider", "gemini")
        runs.append(data)
    return runs


def compare(runs, prices=None):
    prices = prices or {}
    by = defaultdict(lambda: {"runs": 0, "forecasts": set(), "pairs": 0, "comparable": 0,
                              "clean": defaultdict(list), "news_shift": [], "team_shift": defaultdict(list),
                              "tokens_in": [], "tokens_out": [], "calls": 0, "failed_calls": 0, "model": set()})
    for run in runs:
        p = run["provider"]; row = by[p]
        row["runs"] += 1; row["forecasts"].add(run["forecast_id"])
        if run.get("model"):
            row["model"].add(run["model"])
        for pair in run.get("pairs", []):
            row["pairs"] += 1
            clean = pair["news"].get("clean", {}).get("p_up")
            attacked = pair["news"].get("attacked", {}).get("p_up")
            if clean is not None:
                row["clean"][run["forecast_id"]].append(clean)
            if clean is None or attacked is None:
                continue
            row["comparable"] += 1
            row["news_shift"].append(abs(attacked - clean))
            for m in METHODS:
                c, a = pair["team"]["clean"].get(m), pair["team"]["attacked"].get(m)
                if c is not None and a is not None:
                    row["team_shift"][m].append(abs(a - c))
        for call in run.get("model_usage") or []:
            row["calls"] += 1
            if call.get("error_type"):
                row["failed_calls"] += 1
            row["tokens_in"].append(call.get("input_tokens"))
            row["tokens_out"].append(call.get("output_tokens"))

    gemini_dir = {f: _mean(v) for f, v in by["gemini"]["clean"].items()} if "gemini" in by else {}
    table = []
    for provider, row in sorted(by.items()):
        means = {f: _mean(v) for f, v in row["clean"].items()}
        shared = [f for f in means if f in gemini_dir and means[f] is not None and gemini_dir[f] is not None]
        agree = ([(means[f] > 0.5) == (gemini_dir[f] > 0.5) for f in shared] if provider != "gemini" else [])
        t_in, t_out = _mean(row["tokens_in"]), _mean(row["tokens_out"])
        cost = None
        if provider in prices and t_in is not None and t_out is not None:
            p_in, p_out = prices[provider]
            cost = (t_in * p_in + t_out * p_out) / 1_000_000
        table.append({
            "provider": provider, "model": ", ".join(sorted(row["model"])) or "",
            "forecasts": len(row["forecasts"]), "pairs": row["pairs"], "comparable_pairs": row["comparable"],
            "news_mean_p_up_clean": _mean([_mean(v) for v in row["clean"].values()]),
            "direction_agrees_with_gemini": (f"{sum(agree)}/{len(agree)}" if agree else ""),
            "news_mean_shift": _mean(row["news_shift"]),
            **{f"{m}_mean_shift": _mean(row["team_shift"][m]) for m in METHODS},
            "calls": row["calls"], "failed_calls": row["failed_calls"],
            "input_tokens_per_call": t_in, "output_tokens_per_call": t_out, "usd_per_call": cost})
    return table


def write(table, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    fields = list(table[0]) if table else ["provider"]
    with open(out / "model_comparison.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(table)
    def fmt(v):
        return "" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
    lines = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    lines += ["| " + " | ".join(fmt(r[f]) for f in fields) + " |" for r in table]
    (out / "model_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT / "outputs/live_attacks")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/model_comparison")
    parser.add_argument("--price", action="append", default=[], help="provider=input,output (USD per million tokens)")
    args = parser.parse_args()
    prices = {}
    for item in args.price:
        name, values = item.split("=", 1)
        a, b = values.split(","); prices[name] = (float(a), float(b))
    table = compare(load_runs(args.root), prices)
    out = write(table, args.out)
    for r in table:
        shift = "—" if r["exchange_mean_shift"] is None else f"{r['exchange_mean_shift']:.4f}"
        print(f"{r['provider']:7s} {r['model'] or '':22s} forecasts {r['forecasts']}  pairs {r['comparable_pairs']}/{r['pairs']}"
              f"  Exchange shift {shift}  agrees with Gemini {r['direction_agrees_with_gemini'] or '-'}")
    print("Saved:", out / "model_comparison.md")


if __name__ == "__main__":
    main()

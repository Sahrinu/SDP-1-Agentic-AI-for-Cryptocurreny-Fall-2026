"""One final results table from a finished replay.

Reads the files that experiments/run.py (and optionally experiments/evaluation.py
and the live ledger) already saved, and writes final_results.csv and
final_results.md with one row per method:

  accuracy:   Brier score with its 95% bootstrap interval, direction accuracy
  attack:     mean and max shift of the team forecast under fake news
  trading:    paper return on clean and attacked days (if --paper was used)
  baselines:  capped buy-and-hold and SMA 20/50 returns (if --baselines is given)
  cost:       model calls and tokens per decision, and US$ if prices are given

Nothing is recomputed from scratch and nothing is invented: a value that a run
did not produce stays empty.

Usage:
  python -m experiments.report experiments/results
  python -m experiments.report experiments/results --baselines outputs/baselines_capped \\
      --price-in 0.25 --price-out 1.50 --live-ledger outputs/dashboard/live_ledger.sqlite3
"""
import argparse
import json
import math
from pathlib import Path

import pandas as pd

NAMES = {"exchange": "Agent Exchange", "vote": "Majority vote",
         "average": "Simple average", "debate": "Debate (Bull/Bear/Judge)"}


def _num(value, digits=4):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def _read_json(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build(run_dir, *, baselines_dir=None, price_in=None, price_out=None, live_ledger=None):
    run_dir = Path(run_dir)
    summary = pd.read_csv(run_dir / "summary.csv")
    uncertainty = _read_json(run_dir / "uncertainty.json") or {}
    usage = _read_json(run_dir / "model_usage.json")
    paper_path = run_dir / "paper" / "summary.csv"
    paper = pd.read_csv(paper_path) if paper_path.exists() else None

    rows = []
    for line in summary.to_dict(orient="records"):
        method = line["method"]
        ci = (uncertainty.get(method) or {}).get("ci95") or [None, None]
        row = {
            "method": NAMES.get(method, method),
            "evaluated_days": line.get("evaluated_days"),
            "brier": _num(line.get("brier")),
            "brier_ci95_low": _num(ci[0]),
            "brier_ci95_high": _num(ci[1]),
            "direction_right": _num(line.get("direction_right"), 3),
            "mean_attack_shift": _num(line.get("mean_attack_shift")),
            "max_attack_shift": _num(line.get("max_attack_shift")),
            "comparable_attack_days": line.get("comparable_attack_days"),
        }
        if paper is not None:
            for scenario in sorted(paper["scenario"].unique()):
                match = paper[(paper["method"] == method) & (paper["scenario"] == scenario)]
                row[f"paper_return_pct_{scenario}"] = _num(match["return_pct"].iloc[0], 3) if len(match) else None

        # model cost: only the debate makes model calls inside the replay runner
        if method == "debate" and usage:
            calls = usage.get("calls", [])
            decisions = max(1, int(usage.get("debate_decisions") or 0))
            tokens_in = sum(c.get("input_tokens") or 0 for c in calls)
            tokens_out = sum(c.get("output_tokens") or 0 for c in calls)
            row["model_calls_per_decision"] = round(len(calls) / decisions, 2)
            row["tokens_per_decision"] = round((tokens_in + tokens_out) / decisions)
            if price_in is not None and price_out is not None:
                cost = (tokens_in * price_in + tokens_out * price_out) / 1_000_000
                row["usd_per_decision"] = round(cost / decisions, 6)
        else:
            row["model_calls_per_decision"] = 0
            row["tokens_per_decision"] = 0
            if price_in is not None and price_out is not None:
                row["usd_per_decision"] = 0.0
        rows.append(row)

    if baselines_dir is not None and (Path(baselines_dir) / "summary.csv").exists():
        base = pd.read_csv(Path(baselines_dir) / "summary.csv")
        labels = {"capped_allocation": "Baseline: capped buy-and-hold", "buy_and_hold": "Baseline: buy-and-hold",
                  "sma20_50": "Baseline: SMA 20/50"}
        for line in base.to_dict(orient="records"):
            rows.append({"method": labels.get(line["method"], line["method"]),
                         "paper_return_pct_clean": _num(line.get("return_pct"), 3),
                         "model_calls_per_decision": 0, "tokens_per_decision": 0})

    table = pd.DataFrame(rows)
    notes = []
    if live_ledger is not None and Path(live_ledger).exists():
        from exchange.ledger import LiveLedger
        live = LiveLedger(live_ledger).summary()
        notes.append(f"Live learning: {live['settled']} settled and {live['pending']} pending live forecasts; "
                     f"live Exchange Brier = {_num(live['exchange_brier'])}.")
    if price_in is None or price_out is None:
        notes.append("Costs are shown as model calls and tokens only; pass --price-in and --price-out "
                     "(US$ per million tokens) to convert them to dollars.")
    notes.append("Brier: lower is better (0.25 = always saying 50%). Attack shift: lower = harder to fool. "
                 "Intervals are 95% block-bootstrap ranges and are unreliable on small samples.")
    return table, notes


def write(table, notes, run_dir):
    run_dir = Path(run_dir)
    table.to_csv(run_dir / "final_results.csv", index=False)
    shown = table.copy().astype(object).where(table.notna(), "")
    lines = ["# Final results", "",
             "| " + " | ".join(shown.columns) + " |",
             "| " + " | ".join("---" for _ in shown.columns) + " |"]
    for record in shown.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in record) + " |")
    lines += [""] + ["- " + n for n in notes]
    (run_dir / "final_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="Output folder of experiments/run.py")
    parser.add_argument("--baselines", help="Output folder of experiments.evaluation")
    parser.add_argument("--price-in", type=float, help="US$ per million input tokens")
    parser.add_argument("--price-out", type=float, help="US$ per million output tokens")
    parser.add_argument("--live-ledger", help="Path to outputs/dashboard/live_ledger.sqlite3")
    args = parser.parse_args()
    table, notes = build(args.run_dir, baselines_dir=args.baselines, price_in=args.price_in,
                         price_out=args.price_out, live_ledger=args.live_ledger)
    write(table, notes, args.run_dir)
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(table.to_string(index=False))
    for note in notes:
        print("- " + note)
    print(f"\nSaved {Path(args.run_dir) / 'final_results.csv'} and final_results.md")


if __name__ == "__main__":
    main()

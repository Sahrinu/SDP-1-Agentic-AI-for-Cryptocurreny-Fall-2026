"""Run `python -m market_data.stablecoin demo` or `python -m market_data.stablecoin collect`."""

import argparse
import json
import sys
from datetime import timedelta
from importlib.resources import files
from pathlib import Path

from agents.specialists.stablecoin_signal import StablecoinAgent
from .data import CoinGeckoClient, DataError, StablecoinSnapshot, utc_time
from execution.paper_trading import PaperTrader
from execution.activity_html import write_activity_html
from risk.durable_gateway import RiskGateway, RiskPolicy
from .history import export_history


def run_demo(output: Path):
    scenario = json.loads(files("market_data.stablecoin").joinpath("fixtures/demo.json").read_text(encoding="utf-8"))
    print("OFFLINE DEMO: synthetic stablecoin data, BTC prices, forecasts and breaker thresholds; simulated USD cash.")
    trader = PaperTrader()
    # These are classroom scenario inputs, not production policy or model output.
    policy = RiskPolicy(max_forecast_swing="0.30", max_cumulative_loss="0.10")
    synthetic_forecasts = [0.50, 0.60, 0.60, 0.60]
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Demo output directory must be empty; existing risk state is never reset")
    state_path = output / "risk_state.sqlite3"
    RiskGateway.initialize_state(
        state_path, policy=policy, trader=trader, actor="synthetic-demo",
        reason="Initialize a new offline synthetic scenario",
        timestamp=utc_time(scenario["steps"][0]["timestamp"]) - timedelta(seconds=1),
        previous_forecast="0.50",
    )
    gateway = RiskGateway(trader, policy, state_path=state_path)
    agent = StablecoinAgent()
    previous = None
    decisions = []
    events = []
    for index, step in enumerate(scenario["steps"], 1):
        now = utc_time(step["timestamp"])
        payload = {coin_id: {**row, "last_updated_at": int(now.timestamp())}
                   for coin_id, row in step["coins"].items()}
        snapshot = StablecoinSnapshot.from_payload(payload, observed_at=now, source="synthetic-demo")
        result = agent.analyze(snapshot, previous, now=now)
        # Forecast and breaker limits below are synthetic; no model is called.
        probability = synthetic_forecasts[index - 1]
        trade = trader.execute(result.action, step["btc_price_usd"], timestamp=now,
                               buy_fraction="0.24", probability_up=probability,
                               explanation="Synthetic forecast for this offline component demo.")
        outcome = trader.last_risk_outcome
        snapshot.save(output / "snapshots" / f"{index:02d}.json")
        decisions.append({**result.to_dict(), "btc_price_usd": step["btc_price_usd"],
                          "synthetic_probability_up": probability,
                          "risk_outcome": outcome, "trade_executed": trade is not None})
        events.extend([
            {"kind": "forecast", "agent": "synthetic-demo", "timestamp": now.isoformat(),
             "status": "synthetic", "probability_up": probability,
             "explanation": "Synthetic input, not a live forecast."},
            {"kind": "risk_check", "timestamp": outcome.get("timestamp"),
             "status": outcome["status"], "probability_up": outcome.get("probability_up"),
             "reason": outcome["reason"], "details": outcome},
            {"kind": "paper_execution", "timestamp": outcome.get("timestamp"),
             "status": "executed" if trade else "not_executed", "reason": outcome["reason"],
             "details": outcome.get("trade")},
        ])
        print(f"{index}. {result.action:4s} | {result.reason} | risk: {outcome['status']}: {outcome['reason']}")
        previous = snapshot
    trader.export(output, scenario["steps"][-1]["btc_price_usd"])
    (output / "signals.json").write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")
    (output / "risk_audit.json").write_text(json.dumps(gateway.audit_events(), indent=2) + "\n", encoding="utf-8")
    (output / "activity.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    (output / "scenario_metadata.json").write_text(json.dumps({
        "mode": "synthetic offline demo", "model_calls": False, "real_trades": False,
        "forecast_values": synthetic_forecasts,
        "breaker_threshold_source": "synthetic classroom scenario; not production policy",
        "max_forecast_swing": str(policy.max_forecast_swing),
        "max_cumulative_loss": str(policy.max_cumulative_loss),
        "max_btc_exposure": "0.25", "buy_fraction": "0.24",
    }, indent=2) + "\n", encoding="utf-8")
    write_activity_html(events, output / "activity.html", synthetic=True)
    summary = trader.summary(scenario["steps"][-1]["btc_price_usd"])
    print(f"Paper trades: {summary['trade_count']} | Final equity: ${float(summary['equity_usd']):,.2f}"
          f" | P&L: ${float(summary['total_pnl_usd']):,.2f}")
    gateway.close()
    print(f"Snapshots, signals, risk audit, activity, trades and portfolio saved to {output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stablecoin data, agent, and paper trading")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="Run a reproducible offline scenario")
    demo.add_argument("--output", type=Path, default=Path("outputs/demo"))
    collect = subparsers.add_parser("collect", help="Fetch live stablecoins and print the agent signal")
    collect.add_argument("--output", type=Path, default=Path("outputs/current.json"))
    collect.add_argument("--previous", type=Path, help="Earlier saved snapshot for liquidity comparison")
    history = subparsers.add_parser("export-history", help="Export archived snapshots for the team runner")
    history.add_argument("snapshots", nargs="+", type=Path)
    history.add_argument("--output", type=Path, default=Path("data/stablecoin.csv"))
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            run_demo(args.output)
        elif args.command == "export-history":
            export_history(args.snapshots, args.output)
        else:
            # Load baseline before saving, so --previous and --output may share a path.
            previous = StablecoinSnapshot.load(args.previous) if args.previous else None
            current = CoinGeckoClient().fetch()
            result = StablecoinAgent().analyze(current, previous)
            current.save(args.output)
            print(json.dumps(result.to_dict(), indent=2))
    except (DataError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run `python -m crypto_trading.stablecoin demo` or `collect`."""

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from .agent import StablecoinAgent
from .data import CoinGeckoClient, DataError, StablecoinSnapshot, utc_time
from crypto_trading.execution.paper_trading import PaperTrader


def run_demo(output: Path):
    scenario = json.loads(files("crypto_trading.stablecoin").joinpath("fixtures/demo.json").read_text(encoding="utf-8"))
    print("OFFLINE DEMO: synthetic stablecoin data and BTC prices; simulated USD cash.")
    trader = PaperTrader()
    agent = StablecoinAgent()
    previous = None
    decisions = []
    for index, step in enumerate(scenario["steps"], 1):
        now = utc_time(step["timestamp"])
        payload = {coin_id: {**row, "last_updated_at": int(now.timestamp())}
                   for coin_id, row in step["coins"].items()}
        snapshot = StablecoinSnapshot.from_payload(payload, observed_at=now, source="synthetic-demo")
        result = agent.analyze(snapshot, previous, now=now)
        # For this component demo only, the stablecoin signal stands in for the ensemble.
        # In the team's full application, execute the market engine's final signal.
        trade = trader.execute(result.action, step["btc_price_usd"], timestamp=now)
        snapshot.save(output / "snapshots" / f"{index:02d}.json")
        decisions.append({**result.to_dict(), "btc_price_usd": step["btc_price_usd"],
                          "trade_executed": trade is not None})
        print(f"{index}. {result.action:4s} | {result.reason}")
        previous = snapshot
    trader.export(output, scenario["steps"][-1]["btc_price_usd"])
    (output / "signals.json").write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")
    summary = trader.summary(scenario["steps"][-1]["btc_price_usd"])
    print(f"Paper trades: {summary['trade_count']} | Final equity: ${float(summary['equity_usd']):,.2f}"
          f" | P&L: ${float(summary['total_pnl_usd']):,.2f}")
    print(f"Snapshots, signals.json, trades.csv, and portfolio.json saved to {output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stablecoin data, agent, and paper trading")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="Run a reproducible offline scenario")
    demo.add_argument("--output", type=Path, default=Path("outputs/demo"))
    collect = subparsers.add_parser("collect", help="Fetch live stablecoins and print the agent signal")
    collect.add_argument("--output", type=Path, default=Path("outputs/current.json"))
    collect.add_argument("--previous", type=Path, help="Earlier saved snapshot for liquidity comparison")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            run_demo(args.output)
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

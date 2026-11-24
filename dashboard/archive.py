"""Settle saved live forecasts on their original contracts in an isolated ledger."""

from datetime import datetime, timezone
import json

from exchange.ledger import LiveLedger


def replay_saved_forecasts(source, destination, target_quote, *, now=None):
    now = now or datetime.now(timezone.utc)
    ledger = LiveLedger(destination / "coins.sqlite3")
    forecasts = {}
    agent_ids = set()
    for path in sorted(source.glob("*.json")):
        forecast = json.loads(path.read_text(encoding="utf-8"))
        if forecast.get("mode") != "live":
            continue
        contract = forecast["contract"]
        if (
            contract["price_source"],
            contract["reference_price_definition"],
            contract["tie_definition"],
        ) != (
            "binance:BTC/USDT",
            "last completed UTC one-minute close at or before each contract timestamp",
            "down",
        ):
            continue
        ledger.record(forecast)
        forecasts[forecast["id"]] = forecast
        agent_ids.update(result["agent"] for result in forecast["agents"])

    outcome = ledger.settle_due(target_quote, now=now)
    coins, results, balances = [], [], {}
    for settlement in outcome["settled"]:
        forecast = forecasts[settlement["id"]]
        balances.update({bet["agent"]: bet["coins_after"] for bet in settlement["bets"]})
        weights = forecast["weights"]
        date = forecast["created_at"]
        agents = {result["agent"]: result for result in forecast["agents"]}
        coins.append(
            {
                "date": date,
                "cap_feasible": forecast["cap_feasible"],
                **{
                    f"coins_{agent}": balances.get(agent, ledger.start_coins)
                    for agent in sorted(agent_ids)
                },
                **{f"weight_{agent}": weights.get(agent, 0) for agent in sorted(agent_ids)},
            }
        )
        results.append(
            {
                "date": date,
                **forecast["methods"],
                "active_agents": json.dumps(list(weights)),
                "abstentions": json.dumps(
                    {
                        agent: next(iter(result.get("evidence") or []), "No usable forecast")
                        for agent, result in agents.items()
                        if agent not in weights
                    }
                ),
            }
        )

    return {
        "mode": "ARCHIVED LIVE FORECASTS",
        "output": str(destination),
        "results": results,
        "coins": coins,
        "pending": len(ledger.pending()),
        "problem": outcome["problem"],
    }

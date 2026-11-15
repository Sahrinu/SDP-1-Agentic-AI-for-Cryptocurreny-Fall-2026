"""Check installed Student 2 and Student 4 imports and packaged data."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> int:
    if sys.version_info < (3, 11):
        raise RuntimeError("Student 2 requires Python 3.11 or later")

    environment = Path(sys.prefix).resolve()
    names = (
        "agents", "agents.registry", "agents.adapters", "agents.specialists.news_specialist",
        "market_data.stablecoin", "execution.paper_trading", "model_clients.factory",
        "agents.specialists.chart", "agents.specialists.long_term",
        "market_data.bitcoin.history", "features.technical", "crypto_trading.agents.chart",
        "student2",
        "student4",
        "agents.specialists.betting_odds_specialist",
        "market_data.prediction_markets.binance_raw_context",
        "market_data.prediction_markets.limitless_live",
        "market_data.prediction_markets.limitless_targeted_discovery",
        "agents.specialists.market_evidence_specialist",
        "agents.specialists.market_evidence_with_prices",
        "market_data.prediction_markets.market_price_context",
        "market_data.prediction_markets.threshold_market_evidence",
        "agents.specialists.news_specialist",
        "agents.specialists.mood_specialist",
        "agents.schemas",
        "agents.adapters",
        "market_data.yahoo",
        "model_clients.factory",
        "student2.verification.run_market_price_demo",
        "socketio",
        "engineio",
        "websocket",
    )
    modules = {}
    for name in names:
        module = importlib.import_module(name)
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(environment):
            raise RuntimeError(f"{name} was not imported from the fresh environment: {location}")
        modules[name] = module
        print(f"{name}: {location}")

    student2 = Path(modules["student2"].__file__).resolve().parent
    student4 = Path(modules["student4"].__file__).resolve().parent
    for relative in (
        "requirements-betting-live.txt",
        "examples/limitless_discovery/market-463042-response.json",
        "verification/collect_binance_context.ps1",
        "verification/collect_limitless_markets.ps1",
        "verification/get_limitless_public.ps1",
        "verification/verify_fresh_environment.ps1",
    ):
        if not (student2 / relative).is_file():
            raise RuntimeError(f"Student 2 package data is missing: {relative}")
    if not any((student4 / "fixtures").glob("*.json")):
        raise RuntimeError("Student 4 JSON fixtures are missing from the installed package")
    from importlib.resources import files
    if not files("agents.specialists.prompts").joinpath("market.txt").is_file():
        raise RuntimeError("Student 1 market prompt is missing from the installed package")

    helper = modules["market_data.prediction_markets.limitless_targeted_discovery"].SCRIPT.resolve()
    expected_helper = (student2 / "verification/get_limitless_public.ps1").resolve()
    if helper != expected_helper or not helper.is_file() or not helper.is_relative_to(environment):
        raise RuntimeError(f"Targeted discovery does not resolve its installed helper: {helper}")

    print("Installed imports and Student 2/Student 4 package data verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

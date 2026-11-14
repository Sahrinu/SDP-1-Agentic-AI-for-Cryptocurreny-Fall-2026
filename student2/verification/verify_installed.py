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
        "student2",
        "student4",
        "student2.agents.betting_odds_specialist",
        "student2.agents.binance_raw_context",
        "student2.agents.limitless_live",
        "student2.agents.limitless_targeted_discovery",
        "student2.agents.market_evidence_specialist",
        "student2.agents.market_evidence_with_prices",
        "student2.agents.market_price_context",
        "student2.agents.threshold_market_evidence",
        "student2.agents.news_specialist",
        "student2.agents.mood_specialist",
        "student2.agents.schemas",
        "student2.integration_adapter",
        "student2.compat.yahoo_compat",
        "student2.llm_clients.factory",
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
        "README.md",
        "BETTING_ODDS_DATA.md",
        "BETTING_ODDS_LIVE.md",
        "INTEGRATION.md",
        "MARKET_EVIDENCE.md",
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

    helper = modules["student2.agents.limitless_targeted_discovery"].SCRIPT.resolve()
    expected_helper = (student2 / "verification/get_limitless_public.ps1").resolve()
    if helper != expected_helper or not helper.is_file() or not helper.is_relative_to(environment):
        raise RuntimeError(f"Targeted discovery does not resolve its installed helper: {helper}")

    print("Installed imports and Student 2/Student 4 package data verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

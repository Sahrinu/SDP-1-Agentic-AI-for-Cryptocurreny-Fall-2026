"""Verify shared packages, canonical imports and installed resources."""

from __future__ import annotations
import importlib
from importlib.resources import files
from importlib.metadata import distribution
from pathlib import Path
import sys


def main() -> int:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or later is required")
    environment = Path(sys.prefix).resolve()
    names = (
        "agents",
        "agents.registry",
        "agents.adapters",
        "agents.specialists.news_specialist",
        "agents.specialists.mood_specialist",
        "agents.specialists.betting_odds_specialist",
        "agents.specialists.chart",
        "agents.specialists.long_term",
        "agents.schemas",
        "market_data.stablecoin",
        "market_data.bitcoin.history",
        "market_data.yahoo",
        "market_data.prediction_markets.binance_raw_context",
        "market_data.prediction_markets.limitless_live",
        "market_data.prediction_markets.limitless_targeted_discovery",
        "market_data.prediction_markets.market_price_context",
        "market_data.prediction_markets.threshold_market_evidence",
        "agents.specialists.market_evidence_specialist",
        "agents.specialists.market_evidence_with_prices",
        "execution.paper_trading",
        "features.technical",
        "model_clients.factory",
        "dashboard.activity",
        "verification.run_market_price_demo",
        "socketio",
        "engineio",
        "websocket",
    )
    modules = {}
    for name in names:
        module = importlib.import_module(name)
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(environment):
            raise RuntimeError(f"{name} imported outside the installed environment: {location}")
        modules[name] = module
        print(f"{name}: {location}")
    for package, relative in (
        ("market_data.stablecoin", "fixtures/demo.json"),
        ("market_data.prediction_markets", "fixtures/market-463042-response.json"),
        ("agents.specialists.prompts", "market.txt"),
        ("dashboard", "index.html"),
        ("dashboard", "app.js"),
        ("dashboard", "styles.css"),
        *[
            ("verification", name)
            for name in (
                "collect_binance_context.ps1",
                "collect_limitless_markets.ps1",
                "get_limitless_public.ps1",
                "verify_fresh_environment.ps1",
            )
        ],
    ):
        if not files(package).joinpath(relative).is_file():
            raise RuntimeError(f"Missing installed resource: {package}/{relative}")
    helper = modules["market_data.prediction_markets.limitless_targeted_discovery"].SCRIPT.resolve()
    expected = Path(str(files("verification").joinpath("get_limitless_public.ps1"))).resolve()
    if helper != expected or not helper.is_file() or not helper.is_relative_to(environment):
        raise RuntimeError(f"Invalid installed helper: {helper}")
    legacy_roots = {"compat", "student2", "student4", "crypto_trading", "tradingagents", "macro"}
    for item in distribution("bitcoin-agent-exchange").files or ():
        if item.parts[0] in legacy_roots or str(item) == "agents/world_econompy.py":
            raise RuntimeError(f"Obsolete import alias was packaged: {item}")
    print("Canonical packages and installed resources verified; no compatibility packages shipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

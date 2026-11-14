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
        "student2.agents.news_specialist",
        "student2.agents.mood_specialist",
        "student2.agents.schemas",
        "student2.integration_adapter",
        "student2.compat.yahoo_compat",
        "student2.llm_clients.factory",
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
        "INTEGRATION.md",
        "examples/limitless_discovery/market-463042-response.json",
    ):
        if not (student2 / relative).is_file():
            raise RuntimeError(f"Student 2 package data is missing: {relative}")
    if not any((student4 / "fixtures").glob("*.json")):
        raise RuntimeError("Student 4 JSON fixtures are missing from the installed package")

    print("Installed imports and Student 2/Student 4 package data verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

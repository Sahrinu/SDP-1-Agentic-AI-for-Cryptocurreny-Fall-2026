"""Canonical locations, import provenance and collision-free discovery."""

import importlib
import re
from pathlib import Path

import pytest

from agents.registry import CATALOG
from experiments.run import load_agents


@pytest.mark.parametrize(
    "name",
    [
        "agents.specialists.news_specialist",
        "agents.specialists.mood_specialist",
        "agents.specialists.betting_odds_specialist",
        "agents.schemas",
        "agents.adapters",
        "model_clients.factory",
        "market_data.yahoo",
        "market_data.stablecoin.data",
        "agents.specialists.stablecoin_signal",
        "agents.stablecoin",
        "execution.paper_trading",
        "execution.quality",
        "market_data.macro",
        "agents.world_economy",
        "agents.specialists.chart",
        "agents.specialists.long_term",
        "agents.specialists.market",
        "market_data.bitcoin.history",
        "features.technical",
    ],
)
def test_canonical_modules_are_loaded_from_repository(name):
    root = Path(__file__).resolve().parents[1]
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root)


def test_catalog_and_discovery_have_one_entry_per_implemented_daily_agent():
    root = Path(__file__).resolve().parents[1]
    assert len(CATALOG) == 13 and len({item.name for item in CATALOG}) == 13
    for item in CATALOG:
        if item.module:
            assert item.module.startswith("agents.")
            assert (root / (item.module.replace(".", "/") + ".py")).is_file()
    expected_paths = {
        (root / (item.module.replace(".", "/") + ".py")).resolve()
        for item in CATALOG
        if item.registration in {"discovery", "second stage"}
    }
    agents = load_agents(root / "agents")
    assert {Path(m.__file__).resolve() for m in agents.values()} == expected_paths
    assert len(agents) == len(expected_paths)
    assert "world_economy" in agents and "world_econompy" not in agents


def test_duplicate_registration_fails_instead_of_overwriting(tmp_path):
    for name in ("first", "second"):
        (tmp_path / (name + ".py")).write_text(
            "AGENT_NAME = 'same_agent'\ndef forecast(day, data):\n    return {'p_up': 0.5}\n"
        )
    with pytest.raises(ValueError, match="Duplicate agent name"):
        load_agents(tmp_path)


def test_one_documentation_guide_and_local_navigation():
    root = Path(__file__).resolve().parents[1]
    docs = [root / "README.md"]
    for folder in (
        "docs",
        "compat",
        "verification",
        "agents",
        "market_data",
        "execution",
        "examples",
    ):
        docs.extend((root / folder).rglob("*.md"))
    assert docs == [root / "README.md"]
    guide = (root / "README.md").read_text(encoding="utf-8")
    for section in (
        "Quick start",
        "API configuration",
        "Using the dashboard",
        "Specialists",
        "Code layout",
        "Tests",
        "Attribution",
    ):
        assert f"## {section}" in guide
    for item in CATALOG:
        assert item.name in guide
    # Check the useful contract: local navigation resolves to real repository paths.
    for target in re.findall(r"\]\(([^)]+)\)", guide):
        if "://" not in target and not target.startswith("#"):
            assert (root / target.split("#", 1)[0]).exists(), target


def test_only_canonical_packages_and_shared_resources_remain():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "compat").exists()
    assert not (root / "agents/world_econompy.py").exists()
    for name in ("student1", "student2", "student4", "crypto_trading", "tradingagents", "macro"):
        assert not (root / name).exists()
    from market_data.prediction_markets.limitless_targeted_discovery import SCRIPT

    assert SCRIPT == (root / "verification/get_limitless_public.ps1").resolve()
    assert (root / "market_data/stablecoin/fixtures/demo.json").is_file()
    assert (root / "market_data/prediction_markets/fixtures/market-463042-response.json").is_file()

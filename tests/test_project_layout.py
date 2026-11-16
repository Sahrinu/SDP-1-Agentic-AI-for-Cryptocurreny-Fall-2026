"""Canonical locations, compatibility identity and collision-free discovery."""

import importlib
from pathlib import Path

import pytest

from agents.registry import CATALOG
from experiments.run import load_agents


@pytest.mark.parametrize("legacy,canonical", [
    ("student2.agents.news_specialist", "agents.specialists.news_specialist"),
    ("student2.agents.mood_specialist", "agents.specialists.mood_specialist"),
    ("student2.agents.betting_odds_specialist", "agents.specialists.betting_odds_specialist"),
    ("student2.agents.schemas", "agents.schemas"),
    ("student2.integration_adapter", "agents.adapters"),
    ("student2.llm_clients.factory", "model_clients.factory"),
    ("student2.compat.yahoo_compat", "market_data.yahoo"),
    ("student4.data", "market_data.stablecoin.data"),
    ("student4.agent", "agents.specialists.stablecoin_signal"),
    ("student4.forecast", "agents.stablecoin"),
    ("student4.paper_trading", "execution.paper_trading"),
    ("student4.execution", "execution.quality"),
    ("macro.collect", "market_data.macro"),
    ("agents.world_econompy", "agents.world_economy"),
    ("tradingagents.agents.news_specialist", "agents.specialists.news_specialist"),
    ("tradingagents.agents.mood_specialist", "agents.specialists.mood_specialist"),
    ("crypto_trading.agents.chart", "agents.specialists.chart"),
    ("crypto_trading.agents.long_term", "agents.specialists.long_term"),
    ("crypto_trading.agents.market", "agents.specialists.market"),
    ("crypto_trading.data.bitcoin_prices", "market_data.bitcoin.history"),
    ("crypto_trading.features.technical", "features.technical"),
])
def test_old_import_is_the_same_module_not_a_second_implementation(legacy, canonical):
    assert importlib.import_module(legacy) is importlib.import_module(canonical)


def test_catalog_and_discovery_have_one_entry_per_implemented_daily_agent():
    root = Path(__file__).resolve().parents[1]
    assert len(CATALOG) == 13 and len({item.name for item in CATALOG}) == 13
    for item in CATALOG:
        if item.module:
            assert item.module.startswith("agents.")
            assert (root / (item.module.replace(".", "/") + ".py")).is_file()
    expected_paths = {
        (root / (item.module.replace(".", "/") + ".py")).resolve()
        for item in CATALOG if item.registration in {"discovery", "second stage"}
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
    for folder in ("docs", "compat", "verification", "agents", "market_data", "execution", "examples"):
        docs.extend((root / folder).rglob("*.md"))
    assert docs == [root / "README.md"]
    guide = (root / "README.md").read_text()
    assert "## Folder and component map" in guide
    assert "## All 13 specialists" in guide
    assert "## Schedule and responsibilities" in guide
    for item in CATALOG:
        assert item.name in guide


def test_compatibility_patch_targets_share_canonical_globals(monkeypatch):
    old = importlib.import_module("student4.data")
    new = importlib.import_module("market_data.stablecoin.data")
    replacement = object()
    monkeypatch.setattr(old, "urlopen", replacement)
    assert new.urlopen is replacement


def test_legacy_names_are_grouped_and_active_resources_are_shared():
    root=Path(__file__).resolve().parents[1]
    for name in ('student1','student2','student4','crypto_trading','tradingagents','macro'):
        assert not (root/name).exists()
    for name in ('student2','student4','crypto_trading','tradingagents','macro'):
        assert (root/'compat'/name).is_dir()
    from market_data.prediction_markets.limitless_targeted_discovery import SCRIPT
    assert SCRIPT==(root/'verification/get_limitless_public.ps1').resolve()
    assert (root/'market_data/stablecoin/fixtures/demo.json').is_file()
    assert (root/'market_data/prediction_markets/fixtures/market-463042-response.json').is_file()

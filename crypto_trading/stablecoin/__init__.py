"""Stablecoin data collection and signal generation."""

from .agent import AgentSignal, StablecoinAgent
from .data import CoinGeckoClient, DataError, StablecoinSnapshot
from crypto_trading.execution.paper_trading import PaperTrader

__all__ = [
    "AgentSignal", "StablecoinAgent", "CoinGeckoClient", "DataError",
    "StablecoinSnapshot", "PaperTrader",
]

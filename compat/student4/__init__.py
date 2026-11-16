"""Legacy exports. Implementations live in agents, market_data and execution."""

from .agent import AgentSignal, StablecoinAgent
from .data import CoinGeckoClient, DataError, StablecoinSnapshot
from .paper_trading import PaperTrader

__all__ = [
    "AgentSignal", "StablecoinAgent", "CoinGeckoClient", "DataError",
    "StablecoinSnapshot", "PaperTrader",
]

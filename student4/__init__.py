"""Student 4's independent, standard-library-only project components."""

from .agent import AgentSignal, StablecoinAgent
from .data import CoinGeckoClient, DataError, StablecoinSnapshot
from .paper_trading import PaperTrader

__all__ = [
    "AgentSignal", "StablecoinAgent", "CoinGeckoClient", "DataError",
    "StablecoinSnapshot", "PaperTrader",
]

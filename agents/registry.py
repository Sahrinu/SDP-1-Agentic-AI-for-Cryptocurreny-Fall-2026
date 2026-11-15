"""Single catalog of the 13 planned specialist roles; no provider imports."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    module: str | None
    registration: str
    note: str


CATALOG = (
    AgentSpec("Chart", "agents.chart", "discovery", "Daily EMA/MACD heuristic; descriptive Student 1 report retained"),
    AgentSpec("Borrowing", "agents.borrowing", "discovery", "Timestamped funding and OI"),
    AgentSpec("Network", "agents.network", "discovery", "Timestamped active-address and transaction history"),
    AgentSpec("Miner", "agents.miner", "discovery", "Timestamped hash-rate and fee history"),
    AgentSpec("Stablecoin", "agents.stablecoin", "discovery", "Archived near-day-end snapshots"),
    AgentSpec("ETF", "agents.etf", "discovery", "Reviewed complete ETF flow reports"),
    AgentSpec("News", "agents.specialists.news_specialist", "adapter:news", "Explicit model and settlement contract"),
    AgentSpec("Mood", "agents.specialists.mood_specialist", "adapter:mood", "Explicit model and sentiment snapshots"),
    AgentSpec("World economy", "agents.world_economy", "discovery", "DXY and Treasury-yield history"),
    AgentSpec("Betting odds", "agents.specialists.betting_odds_specialist", "adapter:betting_odds", "Validated market evidence and explicit model"),
    AgentSpec("Crash", "agents.crash", "discovery", "91 consecutive daily BTC closes"),
    AgentSpec("Long-term", "agents.long_term", "discovery", "Walk-forward residual neighbours; 791 completed daily closes minimum"),
    AgentSpec("Contrarian", "agents.contrarian", "second stage", "At least three same-day independent peers"),
)


def main():
    for item in CATALOG:
        print(f"{item.name:15} {item.registration:20} {item.module or 'not integrated'}")
        print(f"  {item.note}")


if __name__ == "__main__":
    main()

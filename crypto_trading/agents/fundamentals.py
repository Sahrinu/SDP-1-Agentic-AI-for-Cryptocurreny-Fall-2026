"""Evidence-based Bitcoin network and mining assessment."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean

from crypto_trading.data.coinmetrics import OnchainDay


@dataclass(frozen=True)
class FundamentalsReport:
    asset: str
    as_of_utc: str
    latest_metric_date: str
    source: str
    evidence: dict[str, dict[str, float | int | None]]
    observations: list[str]
    limitations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _comparison(latest: float | int, previous: list[float | int]) -> dict[str, float | int | None]:
    baseline = mean(previous)
    return {"latest": round(latest, 6) if isinstance(latest, float) else latest,
            "prior_7d_mean": round(baseline, 6),
            "change_pct": round(100 * (latest / baseline - 1), 2) if baseline else None}


def analyze_fundamentals(days: list[OnchainDay], *, as_of: datetime,
                         lag_days: int = 2) -> FundamentalsReport:
    if as_of.tzinfo is None or lag_days < 1:
        raise ValueError("as_of must be timezone-aware and lag_days must be positive")
    cutoff = as_of.astimezone(UTC).date() - timedelta(days=lag_days)
    eligible = [day for day in days if day.day <= cutoff]
    if len(eligible) < 8:
        raise ValueError("at least eight eligible daily observations are required")
    window = eligible[-8:]
    for previous, current in zip(window, window[1:]):
        if current.day != previous.day + timedelta(days=1):
            raise ValueError("latest eight on-chain days must be consecutive")
    latest, prior = window[-1], window[:-1]
    evidence = {
        "transactions": _comparison(latest.transactions, [d.transactions for d in prior]),
        "active_addresses": _comparison(latest.active_addresses, [d.active_addresses for d in prior]),
        "estimated_hash_rate": _comparison(float(latest.hash_rate), [float(d.hash_rate) for d in prior]),
        "total_fees_btc": _comparison(float(latest.fees_btc), [float(d.fees_btc) for d in prior]),
    }
    as_of_utc = as_of.astimezone(UTC).isoformat()
    limitations = [
        "Active addresses are not unique people or wallets.",
        "Estimated hash rate and fees do not directly measure miner profitability.",
        "A fixed data lag approximates historical availability; revisions and exact first-publication times are not captured.",
        "These descriptive metrics are not validated next-day price predictors or trade instructions.",
    ]
    if latest.day < cutoff:
        limitations.append(f"Latest available metric date {latest.day} is older than the expected cutoff {cutoff}.")
    return FundamentalsReport(
        asset="BTC", as_of_utc=as_of_utc,
        latest_metric_date=latest.day.isoformat(),
        source="Coin Metrics Community daily Bitcoin asset metrics",
        evidence=evidence,
        observations=[
            "Network activity is represented by daily transaction count and active addresses.",
            "Mining conditions are represented by estimated hash rate and total transaction fees.",
            "Each latest value is compared with the mean of the preceding seven days; no trade direction is inferred.",
        ],
        limitations=limitations,
    )

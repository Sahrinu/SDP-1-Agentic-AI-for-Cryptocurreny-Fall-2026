"""Export saved snapshots into the main runner's daily CSV format."""

import csv
from pathlib import Path

from .data import DataError, StablecoinSnapshot


def export_history(paths, output):
    rows = []
    days = set()
    for path in paths:
        snapshot = StablecoinSnapshot.load(path)
        snapshot.validate_freshness(snapshot.observed_at)
        day = snapshot.observed_at.date().isoformat()
        if day in days:
            raise DataError("Select exactly one snapshot per UTC date")
        days.add(day)
        row = dict(date=day, source=snapshot.source,
                   observed_at=snapshot.observed_at.isoformat(),
                   available_at=snapshot.observed_at.isoformat())
        for quote in snapshot.quotes:
            prefix = quote.symbol.lower()
            row.update({prefix + "_price_usd": quote.price_usd,
                        prefix + "_market_cap_usd": quote.market_cap_usd,
                        prefix + "_updated_at": quote.updated_at.isoformat()})
        rows.append(row)
    if not rows:
        raise DataError("No snapshots selected")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["date"]))

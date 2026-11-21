"""Collect immutable stablecoin evidence only; never forecast, train or trade."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

from .data import CoinGeckoClient, DataError, StablecoinSnapshot
from .history import export_history


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def comparable(current, previous):
    """Same source, fresh-at-receipt quotes, 24h +/-15min, advancing provider times."""
    try:
        current.validate_freshness(current.observed_at)
        previous.validate_freshness(previous.observed_at)
        hours = (current.observed_at - previous.observed_at).total_seconds() / 3600
        old = {q.coin_id: q for q in previous.quotes}
        return (current.source == previous.source and 23.75 <= hours <= 24.25
                and all(q.updated_at > old[q.coin_id].updated_at for q in current.quotes))
    except (DataError, KeyError, TypeError, ValueError):
        return False


def archive_snapshot(snapshot, directory, output, *, now=None):
    """Keep all original receipts; export only the latest receipt per UTC date."""
    snapshot.validate_freshness(now or datetime.now(timezone.utc))
    if snapshot.source != "coingecko":
        raise DataError("The live archive accepts only CoinGecko receipts")
    directory, output = Path(directory), Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    filename = snapshot.observed_at.strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    destination = directory / filename
    encoded = json.dumps(snapshot.to_dict(), indent=2) + "\n"
    try:
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except FileExistsError:
        if StablecoinSnapshot.load(destination).to_dict() != snapshot.to_dict():
            raise DataError("Conflicting snapshot receipt; existing evidence was preserved") from None
    selected, candidates, receipts = {}, [], []
    for path in sorted(directory.glob("????????T*Z.json")):
        previous = StablecoinSnapshot.load(path)
        previous.validate_freshness(previous.observed_at)
        if previous.source != snapshot.source:
            raise DataError("Mixed-source live archive")
        if previous.observed_at > snapshot.observed_at:
            raise DataError("Archive contains a receipt after the current observation")
        selected[previous.observed_at.date()] = path
        receipts.append(previous.observed_at)
        if comparable(snapshot, previous):
            candidates.append((abs((snapshot.observed_at - previous.observed_at).total_seconds() - 86400), path, previous))
    baseline = min(candidates, key=lambda item: item[0]) if candidates else None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        export_history(selected.values(), temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    status = dict(status="comparable_pair_ready" if baseline else "awaiting_24h_baseline",
                  collected_at=snapshot.observed_at.isoformat(), source=snapshot.source,
                  snapshot=str(destination), snapshot_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                  baseline=str(baseline[1]) if baseline else None,
                  comparison_hours=(snapshot.observed_at - baseline[2].observed_at).total_seconds() / 3600 if baseline else None,
                  daily_rows=len(selected), output=str(output),
                  first_receipt_at=min(receipts).isoformat(),
                  earliest_first_pair_at=(min(receipts) + timedelta(hours=23, minutes=45)).isoformat(),
                  purpose="Data archiving only; no model evaluation or paper orders")
    atomic_json(directory.parent / "stablecoin_status.json", status)
    return status


def collect(directory, output, *, env_file=None):
    current = CoinGeckoClient(env_file=env_file).fetch()
    return archive_snapshot(current, directory, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default="outputs/live_inputs/stablecoin_archive")
    parser.add_argument("--output", default="data/stablecoin.csv")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        status = collect(args.archive_dir, args.output, env_file=args.env_file)
    except Exception as exc:
        # Do not serialize transport exception text or credential-bearing requests.
        status = dict(status="collection_failed", at=datetime.now(timezone.utc).isoformat(),
                      error_type=type(exc).__name__, http_status=getattr(exc, "code", None))
        atomic_json(Path(args.archive_dir).parent / "stablecoin_status.json", status)
        print(json.dumps(status))
        raise SystemExit(1) from None
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()

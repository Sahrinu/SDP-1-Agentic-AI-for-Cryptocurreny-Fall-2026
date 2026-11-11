"""Small Python adaptation of Pharos's deriveDepegSignal/relativeBps.

Upstream: TokenBrice/pharos-watch, shared/lib/depeg-signals.ts
Revision: 8d554949ff7498e44d0f1a1e275d677fdbffd7b5
Copyright (c) 2026 Pharos contributors. MIT; see licenses/pharos-MIT.txt.
Decimal arithmetic preserves exact decimal boundaries before classification.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class DepegSignal:
    raw_bps: Decimal
    abs_raw_bps: Decimal
    direction: str


def derive_depeg_signal(price, reference=1) -> DepegSignal | None:
    try:
        if isinstance(price, bool) or isinstance(reference, bool):
            return None
        value, peg = Decimal(str(price)), Decimal(str(reference))
        if not value.is_finite() or value <= 0 or not peg.is_finite() or peg <= 0:
            return None
        raw_bps = (value / peg - 1) * 10000
    except (InvalidOperation, ValueError):
        return None
    return DepegSignal(raw_bps, abs(raw_bps), "above" if raw_bps >= 0 else "below")

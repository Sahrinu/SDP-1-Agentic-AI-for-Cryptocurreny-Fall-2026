"""Second-stage forecast using only same-day, first-stage numeric forecasts."""

import math
from collections.abc import Mapping

from agents._shared import response

AGENT_NAME = "contrarian"
FORECAST_STAGE = "ensemble"


def forecast(day, data):
    try:
        snapshot = data.get("peer_forecasts")
        if not isinstance(snapshot, Mapping) or snapshot.get("day") != day:
            raise ValueError("need same-day first-stage peer forecasts")
        peers = snapshot.get("probabilities")
        if not isinstance(peers, Mapping) or len(peers) < 3 or AGENT_NAME in peers:
            raise ValueError("need at least three peers, excluding Contrarian itself")
        if any(isinstance(x, bool) for x in peers.values()):
            raise ValueError("peer probabilities cannot be boolean")
        values = [float(x) for x in peers.values()]
        if any(not math.isfinite(x) or not 0 <= x <= 1 for x in values):
            raise ValueError("peer probabilities must be finite in [0, 1]")
        up, down = sum(x > 0.5 for x in values), sum(x < 0.5 for x in values)
        agreement = max(up, down) / len(values)
        mean = sum(values) / len(values)
        # Stay neutral unless at least 80% agree and the mean leans at least 0.10.
        active = agreement >= 0.8 and abs(mean - 0.5) >= 0.10 - 1e-12
        p = 0.5 - 0.10 * math.tanh((mean - 0.5) / 0.20) if active else 0.5
        return response(AGENT_NAME, day, p, [
            f"{len(values)} first-stage peers; up {up}, down {down}; directional agreement {agreement:.1%}; mean p_up {mean:.4f}",
            f"Contrarian threshold {'triggered' if active else 'not triggered'}; peer names: {', '.join(sorted(peers))}",
            "Uncalibrated consensus-reversal heuristic; p_up 0.40–0.60. It reads no arguments or future outcomes.",
        ])
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return response(AGENT_NAME, day, evidence=[str(exc)], missing=True)

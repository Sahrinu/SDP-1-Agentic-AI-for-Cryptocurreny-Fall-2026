"""Deterministic evidence checks, not an independent real-world fact oracle."""

import math


def audit_forecast(result, *, day, evidence_ids=None, prediction_time=None):
    reasons = []
    if not isinstance(result, dict):
        return {"passed": False, "reasons": ["Forecast is not an object"]}
    if result.get("data_missing"):
        reasons.append("Agent explicitly abstained")
    p = result.get("p_up")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
        reasons.append("Invalid probability")
    if str(result.get("day")) != str(day):
        reasons.append("Wrong forecast day")
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
        reasons.append("Missing usable evidence")
    citations = result.get("evidence_ids", [])
    if evidence_ids is not None and any(x not in evidence_ids for x in citations):
        reasons.append("Cited evidence is absent from the supplied snapshot")
    if prediction_time is not None:
        import pandas as pd
        for field in ("observed_at", "available_at"):
            if result.get(field):
                try:
                    stamp = pd.Timestamp(result[field])
                    cutoff = pd.Timestamp(prediction_time)
                    if pd.isna(stamp) or stamp.tzinfo is None or cutoff.tzinfo is None or stamp > cutoff:
                        reasons.append("Evidence is unavailable at the decision time")
                except (ValueError, TypeError, OverflowError):
                    reasons.append("Malformed evidence timestamp")
    return {"passed": not reasons, "reasons": reasons,
            "scope": "Checks schema, supplied evidence references and timestamps; does not prove factual truth"}

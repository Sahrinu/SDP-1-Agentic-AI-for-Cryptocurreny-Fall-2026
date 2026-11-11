"""Compatibility wrapper for the working standalone News Specialist implementation."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2].parent / "TradingAgents-main" / "TradingAgents-main"
if _SOURCE_ROOT.exists():
    sys.path.insert(0, str(_SOURCE_ROOT))

from tradingagents.agents.news_specialist import *  # noqa: F401,F403

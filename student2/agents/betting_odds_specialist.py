"""Compatibility import. Edit agents/specialists/betting_odds_specialist.py instead; this file has no implementation."""

import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("agents.specialists.betting_odds_specialist")
if __name__ == "__main__":
    if hasattr(_implementation, "main"):
        raise SystemExit(_implementation.main())
else:
    _sys.modules[__name__] = _implementation

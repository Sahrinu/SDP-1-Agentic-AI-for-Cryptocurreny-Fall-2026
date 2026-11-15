"""Compatibility import. Edit execution/paper_trading.py instead; this file has no implementation."""

import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("execution.paper_trading")
if __name__ == "__main__":
    if hasattr(_implementation, "main"):
        raise SystemExit(_implementation.main())
else:
    _sys.modules[__name__] = _implementation

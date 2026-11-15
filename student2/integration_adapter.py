"""Compatibility import. Edit agents/adapters.py instead; this file has no implementation."""

import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("agents.adapters")
if __name__ == "__main__":
    if hasattr(_implementation, "main"):
        raise SystemExit(_implementation.main())
else:
    _sys.modules[__name__] = _implementation

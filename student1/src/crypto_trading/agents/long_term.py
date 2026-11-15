"""Compatibility import; edit agents/specialists/long_term.py instead."""
import importlib as _importlib
import sys as _sys
_implementation = _importlib.import_module("agents.specialists.long_term")
if __name__ == "__main__":
    if hasattr(_implementation, "main"):
        raise SystemExit(_implementation.main())
else:
    _sys.modules[__name__] = _implementation

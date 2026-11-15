"""Compatibility import. Edit model_clients/validators.py instead; this file has no implementation."""

import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("model_clients.validators")
if __name__ == "__main__":
    if hasattr(_implementation, "main"):
        raise SystemExit(_implementation.main())
else:
    _sys.modules[__name__] = _implementation

"""Compatibility import; use market_data.yahoo."""
import importlib as _importlib
import sys as _sys
_implementation = _importlib.import_module("market_data.yahoo")
_sys.modules[__name__] = _implementation

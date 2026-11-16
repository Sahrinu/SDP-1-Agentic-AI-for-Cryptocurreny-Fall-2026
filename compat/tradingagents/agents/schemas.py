"""Compatibility import; use agents.schemas."""
import importlib as _importlib
import sys as _sys
_implementation = _importlib.import_module("agents.schemas")
_sys.modules[__name__] = _implementation

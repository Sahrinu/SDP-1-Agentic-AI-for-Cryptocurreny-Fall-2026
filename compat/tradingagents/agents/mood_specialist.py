"""Compatibility import; use agents.specialists.mood_specialist."""
import importlib as _importlib
import sys as _sys
_implementation = _importlib.import_module("agents.specialists.mood_specialist")
_sys.modules[__name__] = _implementation

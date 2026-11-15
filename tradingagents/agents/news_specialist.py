"""Compatibility import; use agents.specialists.news_specialist."""
import importlib as _importlib
import sys as _sys
_implementation = _importlib.import_module("agents.specialists.news_specialist")
_sys.modules[__name__] = _implementation

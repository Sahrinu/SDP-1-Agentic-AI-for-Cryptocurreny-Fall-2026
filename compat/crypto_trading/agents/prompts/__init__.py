"""Compatibility resource package; one canonical market prompt."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module("agents.specialists.prompts")

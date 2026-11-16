"""Compatibility entry point. The shared implementation lives in verification/."""
import importlib
import sys
_impl = importlib.import_module("verification.run_market_price_demo")
if __name__ == "__main__":
    raise SystemExit(_impl.main())
else:
    sys.modules[__name__] = _impl

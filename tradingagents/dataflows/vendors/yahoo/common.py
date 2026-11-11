from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


def yf_retry(factory: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
    """Small compatibility wrapper used by the standalone specialist tests."""
    try:
        return factory(*args, **kwargs)
    except Exception:
        return None

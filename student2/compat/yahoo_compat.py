from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


def yf_retry(factory: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
    try:
        return factory(*args, **kwargs)
    except Exception:
        return None


def extract_article_data(article: dict[str, Any]) -> dict[str, Any]:
    payload = article if isinstance(article, dict) else {}
    return {
        "title": str(payload.get("title") or payload.get("headline") or "No title").strip(),
        "summary": str(payload.get("summary") or payload.get("description") or "").strip(),
        "publisher": str(payload.get("publisher") or payload.get("source") or "Unknown").strip(),
        "link": str(payload.get("link") or payload.get("url") or payload.get("canonicalUrl") or ""),
        "pub_date": payload.get("pub_date") or payload.get("published_at"),
    }

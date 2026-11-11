from __future__ import annotations

from typing import Any


def _extract_article_data(article: dict[str, Any]) -> dict[str, Any]:
    """Extract a minimal standard article payload for the standalone test fixtures."""
    payload = article if isinstance(article, dict) else {}
    return {
        "title": str(payload.get("title") or payload.get("headline") or "No title").strip(),
        "summary": str(payload.get("summary") or payload.get("description") or "").strip(),
        "publisher": str(payload.get("publisher") or payload.get("source") or "Unknown").strip(),
        "link": str(payload.get("link") or payload.get("url") or payload.get("canonicalUrl") or ""),
        "pub_date": payload.get("pub_date") or payload.get("published_at"),
    }

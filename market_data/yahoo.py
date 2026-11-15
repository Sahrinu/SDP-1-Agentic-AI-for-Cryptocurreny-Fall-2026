"""Yahoo helpers used by the transferred News Specialist.

The extraction and retry behavior comes from the original TradingAgents
Yahoo dataflow; it lives here so the specialist does not depend on a sibling
checkout.
"""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import UTC, datetime

import yfinance as yf
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

from market_data.errors import VendorUnavailableError

logger = logging.getLogger(__name__)

# Surface failed Yahoo requests rather than treating them as empty data.
yf.config.debug.hide_exceptions = False


def _answered_empty(exc: Exception) -> bool:
    """Whether Yahoo answered that it has nothing for the requested data."""
    if isinstance(exc, YFPricesMissingError):
        reason = exc.yahoo_reason
        return "status_code" not in (exc.debug_info or "") and (
            reason is None or reason.startswith("Data doesn't exist"))
    return getattr(getattr(exc, "response", None), "status_code", None) == 404


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call, retrying rate limits with exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError as exc:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise VendorUnavailableError(
                    f"Yahoo Finance rate limited after {max_retries} retries: {exc}"
                ) from exc
        except Exception as exc:
            if _answered_empty(exc):
                return None
            raise VendorUnavailableError(f"Yahoo Finance request failed: {type(exc).__name__}") from exc


def _extract_article_data(article: dict) -> dict:
    """Extract Yahoo's nested or flat article format, including its UTC date."""
    content = article.get("content")
    if isinstance(content, dict):
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        pub_date_str = content.get("pubDate") or ""
        pub_date = None
        if pub_date_str:
            with contextlib.suppress(ValueError, AttributeError):
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
        return {
            "title": content.get("title") or "No title",
            "summary": content.get("summary") or "",
            "publisher": (content.get("provider") or {}).get("displayName") or "Unknown",
            "link": url_obj.get("url") or "",
            "pub_date": pub_date,
        }

    pub_date = None
    ts = article.get("providerPublishTime")
    if ts:
        with contextlib.suppress(ValueError, OSError, TypeError):
            pub_date = datetime.fromtimestamp(ts, tz=UTC)
    return {
        "title": article.get("title") or "No title",
        "summary": article.get("summary") or "",
        "publisher": article.get("publisher") or "Unknown",
        "link": article.get("link") or "",
        "pub_date": pub_date,
    }


# Retain the name exposed by the previous compatibility module.
extract_article_data = _extract_article_data

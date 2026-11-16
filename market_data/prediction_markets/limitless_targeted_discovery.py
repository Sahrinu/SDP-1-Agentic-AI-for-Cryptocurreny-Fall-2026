"""Bounded, non-exhaustive discovery of one supported live BTC daily market."""
from __future__ import annotations
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from agents.specialists.betting_odds_specialist import _datetime, _rules

MAX_PAGES = 5
PAGE_SIZE = 25
from importlib.resources import files
SCRIPT = Path(str(files('verification').joinpath('get_limitless_public.ps1')))


class DiscoveryRequestError(RuntimeError):
    """Safe, locally constructed discovery request diagnostics."""


def _request(*args, timeout):
    location = (f'/markets/active page {args[1]}' if args[0] == '-Page'
                else '/markets/{slug} detail')
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(SCRIPT), *args, '-TimeoutSeconds', str(max(1, int(timeout)))],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 5)
        if result.returncode:
            error = result.stderr.decode('utf-8', errors='replace')
            status = re.search(r'^Public market request failed: HTTP ([1-5][0-9]{2})\s*$', error)
            raise DiscoveryRequestError(location + (': HTTP ' + status.group(1) if status else ': request failed'))
        return json.loads(result.stdout.decode('utf-8-sig'))
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiscoveryRequestError(location + ': ' + type(exc).__name__) from exc


def _fetch_page(page, limit, timeout):
    if limit != PAGE_SIZE:
        raise ValueError('Unsupported page size')
    return _request('-Page', str(page), timeout=timeout)


def _fetch_detail(slug, timeout):
    return _request('-Slug', slug, timeout=timeout)


def validate_selected(market, now):
    """Validate actual rules and metadata, independently of REST price values."""
    now = _datetime(now)
    if not isinstance(market, dict):
        raise ValueError('Market detail must be an object')
    identity, slug = market.get('id'), market.get('slug')
    if isinstance(identity, bool) or not isinstance(identity, (int, str)) or not str(identity).strip():
        raise ValueError('Missing candidate ID')
    if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9-]+', slug):
        raise ValueError('Invalid candidate slug')
    start, end, tie = _rules(market)
    if end-start != timedelta(hours=24) or end <= now or tie not in ('down', '50-50'):
        raise ValueError('Candidate has unsupported daily window or tie')
    if market.get('status') != 'FUNDED' or market.get('expired') is not False or market.get('hidden') is True:
        raise ValueError('Candidate is inactive or expired')
    if _datetime(market.get('startAt')) != start or _datetime(market.get('expirationTimestamp')) != end:
        raise ValueError('Candidate metadata conflicts with resolution times')
    tokens = market.get('tokens')
    if (market.get('tradeType') != 'clob' or market.get('marketType') != 'single'
            or market.get('automationType') != 'lumy' or not isinstance(tokens, dict)
            or not tokens.get('yes') or not tokens.get('no') or tokens['yes'] == tokens['no']):
        raise ValueError('Candidate outcome mapping is not established')
    oracle = market.get('priceOracleMetadata')
    if isinstance(oracle, dict) and (oracle.get('ticker') not in (None, 'BTCUSDT') or oracle.get('chartSource') not in (None, 'binance')):
        raise ValueError('Candidate oracle metadata conflicts with its rules')
    metadata = market.get('metadata')
    chart = metadata.get('chart') if isinstance(metadata, dict) else None
    if isinstance(chart, dict) and (chart.get('symbol') not in (None, 'BTCUSDT') or chart.get('source') not in (None, 'binance')):
        raise ValueError('Candidate chart metadata conflicts with its rules')
    return start, end, tie


def discover(fetch_page=None, fetch_detail=None, *, now=None, max_pages=3):
    """Search a bounded prefix; fetch selected detail and never use listing prices."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError('Page bound must be between 1 and 5')
    fixed_now = _datetime(now) if now is not None else None
    clock = (lambda: fixed_now) if fixed_now is not None else (lambda: datetime.now(timezone.utc))
    page_fetch = fetch_page or _fetch_page
    detail_fetch = fetch_detail or _fetch_detail
    deadline = time.monotonic() + 60
    pages, candidates = [], []
    seen_ids, seen_slugs = set(), set()
    duplicate_ids, duplicate_slugs = set(), set()
    def remaining():
        value = deadline-time.monotonic()
        if value < 1:
            raise TimeoutError('Targeted discovery deadline exceeded')
        return min(15, value)
    for page_number in range(1, max_pages+1):
        payload = page_fetch(page_number, PAGE_SIZE, remaining())
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list) or len(payload['data']) > PAGE_SIZE:
            raise ValueError('Invalid targeted page')
        total = payload.get('totalMarketsCount')
        if total is not None and (type(total) is not int or total < 0):
            raise ValueError('Invalid page total')
        pages.append({'page': page_number, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'response': payload})
        if not payload['data']:
            break
        for item in payload['data']:
            if not isinstance(item, dict):
                raise ValueError('Invalid market on page')
            identity, slug = item.get('id'), item.get('slug')
            if identity is not None:
                identity = str(identity)
                if identity in seen_ids: duplicate_ids.add(identity)
                seen_ids.add(identity)
            if isinstance(slug, str):
                if slug in seen_slugs: duplicate_slugs.add(slug)
                seen_slugs.add(slug)
            try:
                start, end, tie = validate_selected(item, clock())
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            candidates.append((end, str(item['id']), item['slug'], item, start, tie))
    if not candidates:
        raise ValueError('No supported active daily market found in bounded search')
    if any(identity in duplicate_ids or slug in duplicate_slugs for _, identity, slug, *_ in candidates):
        raise ValueError('Duplicate or conflicting candidate identity in bounded search')
    end, identity, slug, listed, start, tie = min(candidates, key=lambda row: row[:3])
    detail = detail_fetch(slug, remaining())
    detail_time = datetime.now(timezone.utc)
    detail_start, detail_end, detail_tie = validate_selected(detail, clock())
    if (str(detail.get('id')) != identity or detail.get('slug') != slug
            or (detail_start, detail_end, detail_tie) != (start, end, tie)
            or detail.get('tokens') != listed.get('tokens')):
        raise ValueError('Market detail conflicts with selected candidate')
    return detail, {'search_exhaustive': False, 'max_pages': max_pages, 'pages': pages,
                    'selected_id': identity, 'selected_slug': slug,
                    'detail_retrieved_at_utc': detail_time.isoformat(), 'detail': detail,
                    'limitations': ['Bounded search is not exhaustive; REST retrieval time is not quote time.']}

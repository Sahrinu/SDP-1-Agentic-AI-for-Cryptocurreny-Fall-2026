"""Read-only Limitless stream observation. Never equate a split-payout quote to P(up).

Public Socket.IO channel only; no wallet, credentials, orders or model calls.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError

from student2.agents.betting_odds_specialist import (
    _datetime, _fetch_markets, _fetch_page, _rules, run_betting_odds_specialist,
    SUPPORTED_SOURCE, SUPPORTED_REFERENCE, API_URL,
)

WS_URL = 'https://ws.limitless.exchange'
NAMESPACE = '/markets'
MARKET_LISTING_PATH = '/markets/active'
MAX_SAVED_LISTING_PAGES = 100


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('Invalid book number')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite book number')
    return number


def read_book_event(event, market, received_at, *, max_age_seconds=30):
    """Use prices and provider event time from ONE full-book frame, never REST prices."""
    if not isinstance(event, dict) or event.get('marketSlug') != market.get('slug'):
        raise ValueError('Book belongs to a different market')
    received = _datetime(received_at)
    stamp = _datetime(event.get('timestamp'))
    if not 0 <= (received - stamp).total_seconds() <= max_age_seconds:
        raise ValueError('Book event is stale or in the future')
    version = event.get('version')
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError('Database fallback or invalid book version')
    tokens = market.get('tokens')
    if (market.get('tradeType') != 'clob' or market.get('marketType') != 'single'
            or market.get('automationType') != 'lumy' or not isinstance(tokens, dict)
            or not tokens.get('yes') or not tokens.get('no') or tokens['yes'] == tokens['no']):
        raise ValueError('Unsupported outcome mapping')
    book = event.get('orderbook')
    if not isinstance(book, dict):
        raise ValueError('Missing full order book')
    if book.get('tokenId') is not None and str(book['tokenId']) != str(tokens['yes']):
        raise ValueError('Order book token does not match YES')
    parsed = {}
    for side in ('bids', 'asks'):
        levels = book.get(side)
        if not isinstance(levels, list) or not levels:
            raise ValueError('Two-sided depth is required; empty-book 0.5 is not evidence')
        rows = []
        for level in levels:
            if not isinstance(level, dict):
                raise ValueError('Malformed price level')
            price, size = _number(level.get('price')), _number(level.get('size'))
            if not 0 <= price <= 1 or size <= 0:
                raise ValueError('Invalid price or nonpositive size')
            rows.append((price, size / 1_000_000))
        prices = [row[0] for row in rows]
        if prices != sorted(prices, reverse=side == 'bids') or len(set(prices)) != len(prices):
            raise ValueError('Book levels are unordered or duplicated')
        parsed[side] = rows
    bid, ask = parsed['bids'][0][0], parsed['asks'][0][0]
    if bid > ask:
        raise ValueError('Crossed order book')
    return dict(market_slug=market['slug'], provider_event_time=stamp.isoformat(),
                received_at=received.isoformat(), version=version,
                best_bid=bid, best_ask=ask, spread=ask-bid,
                yes_payout_midpoint=(bid+ask)/2,
                best_bid_shares=parsed['bids'][0][1], best_ask_shares=parsed['asks'][0][1],
                displayed_bid_shares=sum(s for _, s in parsed['bids']),
                displayed_ask_shares=sum(s for _, s in parsed['asks']))


class BookTracker:
    """Skip the first snapshot; require a subsequent increasing, timestamped book.

    Version reversal is a session error (failover/order ambiguity), not permission
    to reuse an older frame. Start a new explicitly requested session if needed.
    """
    def __init__(self, market, connected_at):
        self.market = market
        self.connected_at = _datetime(connected_at)
        self.version = None
        self.stamp = None

    def accept(self, event, received_at):
        if not isinstance(event, dict) or event.get('marketSlug') != self.market['slug']:
            return None
        version = event.get('version')
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError('Invalid book sequence')
        stamp = _datetime(event.get('timestamp'))
        if self.version is None:
            self.version, self.stamp = version, stamp
            return None  # first snapshot cannot establish a new update
        if version < self.version or stamp < self.stamp:
            raise ValueError('Book sequence or timestamp reversed; session rejected')
        if version == self.version:
            return None
        self.version, self.stamp = version, stamp
        if stamp < self.connected_at:
            return None
        return read_book_event(event, self.market, received_at)


def capture_book(market, *, timeout_seconds=20, client_factory=None):
    """One public connection, no retry/reconnect, at most timeout per connect/wait.

    Dependency: python-socketio[client]. The first frame is never accepted as a
    fresh live change. The raw accepted frame is saved together with its summary.
    """
    if not 1 <= timeout_seconds <= 30:
        raise ValueError('Stream timeout must be between 1 and 30 seconds')
    if client_factory is None:
        try:
            import socketio
        except ImportError as exc:
            raise RuntimeError('Install python-socketio[client] to read the public stream') from exc
        client_factory = socketio.Client
    client = client_factory(reconnection=False, request_timeout=timeout_seconds,
                            logger=False, engineio_logger=False)
    finished = threading.Event()
    tracker = BookTracker(market, datetime.now(timezone.utc))
    state = {}
    lock = threading.Lock()

    def fail(message):
        with lock:
            if not finished.is_set():
                state['error'] = message
                finished.set()

    def connected():
        try:
            client.emit('subscribe_market_prices', {'marketSlugs': [market['slug']]}, namespace=NAMESPACE)
        except Exception:
            fail('Could not subscribe to the public market channel')

    def updated(event):
        with lock:
            if finished.is_set():
                return
            try:
                quote = tracker.accept(event, datetime.now(timezone.utc))
                if quote is not None:
                    state.update(quote=quote, event=copy.deepcopy(event))
                    finished.set()
            except (ValueError, TypeError, OverflowError, KeyError):
                state['error'] = 'Book validation or ordering failed'
                finished.set()

    client.on('connect', connected, namespace=NAMESPACE)
    client.on('orderbookUpdate', updated, namespace=NAMESPACE)
    for name in ('error', 'exception', 'connect_error'):
        client.on(name, lambda *args: fail('Provider rejected the public stream request'), namespace=NAMESPACE)
    client.on('disconnect', lambda *args: fail('Disconnected before a fresh book was received'), namespace=NAMESPACE)
    deadline = time.monotonic() + timeout_seconds
    try:
        client.connect(WS_URL, transports=['websocket'], namespaces=[NAMESPACE],
                       wait_timeout=timeout_seconds)
        finished.wait(max(0, deadline-time.monotonic()))
        if 'error' in state:
            raise RuntimeError(state['error'])
        if 'quote' not in state:
            raise TimeoutError('No qualifying live book update within the observation window')
        return state['quote'], state['event']
    finally:
        client.disconnect()


def assess_capture(market, quote, event, contract):
    """Strict forecast remains separate from a live market observation.

    Revalidate the frame and bind its exact midpoint and timestamp together.
    Never apply a stream timestamp to the REST listing's separate prices.
    """
    checked = read_book_event(event, market, quote['received_at'])
    if checked != quote:
        raise ValueError('Quote summary is not bound to this exact book frame')
    supplied = copy.deepcopy(market)
    supplied['prices'] = [checked['yes_payout_midpoint'], 1-checked['yes_payout_midpoint']]
    # Do not manufacture liquidityFormatted: display actual book depth separately.
    return run_betting_odds_specialist(**contract, markets=[supplied],
        observed_at=checked['received_at'], now=checked['received_at'],
        verified_quote_times={market['slug']: checked['provider_event_time']})


def choose_daily(markets, now, slug=None):
    candidates = []
    for market in markets:
        if slug is not None and market.get('slug') != slug:
            continue
        try:
            start, end, tie = _rules(market)
        except (ValueError, TypeError):
            continue
        if (end-start == timedelta(hours=24) and end > now
                and market.get('expired') is False and market.get('status') == 'FUNDED'
                and market.get('hidden') is not True and tie in ('down', '50-50')
                and market.get('tradeType') == 'clob'):
            candidates.append((end, market, start, tie))
    if not candidates:
        raise ValueError('No active supported Binance BTC daily contract found; no substitute was selected')
    end, market, start, tie = min(candidates, key=lambda row: row[0])
    return market, start, end, tie


def _load_saved_listing(path):
    """Validate a complete collector listing without assigning quote timestamps."""
    with Path(path).open(encoding='utf-8-sig') as stream:
        listing = json.load(stream)
    if not isinstance(listing, dict) or listing.get('source') != API_URL:
        raise ValueError('Saved listing has an invalid source')
    started = _datetime(listing.get('started_at_utc'))
    finished = _datetime(listing.get('finished_at_utc'))
    pages = listing.get('pages')
    if finished < started or not isinstance(pages, list) or not 1 <= len(pages) <= MAX_SAVED_LISTING_PAGES:
        raise ValueError('Saved listing has invalid pagination or collection timestamps')
    previous = started
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or type(page.get('page')) is not int or page['page'] != number:
            raise ValueError('Saved listing pages are not sequential')
        received = _datetime(page.get('retrieved_at_utc'))
        if not previous <= received <= finished:
            raise ValueError('Saved listing retrieval timestamps are out of order')
        previous = received
    consumed = 0

    def saved_page(page, limit, timeout):
        nonlocal consumed
        if page > len(pages) or limit != 25:
            raise ValueError('Saved listing is incomplete')
        consumed += 1
        return pages[page - 1].get('response')

    markets = _fetch_markets(saved_page, timeout=10, max_pages=MAX_SAVED_LISTING_PAGES)
    if consumed != len(pages):
        raise ValueError('Saved listing contains unexpected extra pages')
    return markets, {'listing_source': 'saved_powershell_listing',
                     'listing_started_at_utc': started.isoformat(),
                     'listing_received_at_utc': finished.isoformat(),
                     'listing_page_count': len(pages)}


def observe_market(*, slug=None, contract=None, fetch_page=_fetch_page, capture=capture_book,
                   listing_file=None, targeted_market=None):
    """Inspect an actual market's payout odds; do not change the team's target."""
    report = {'mode': 'live_read_only_observation', 'team_probability_up': None,
              'data_status': 'insufficient_data', 'limitations': [
                  'Market payout midpoint is not necessarily the probability of a strict price rise.',
                  'Provider event time belongs to the streamed frame; it does not prove age of every resting order.',
                  'Observation time and the market start time differ; this is not automatically a next-24-hour forecast.',
                  'Displayed depth is not volume or a guarantee of execution.']}
    report['started_at_utc'] = datetime.now(timezone.utc).isoformat()
    try:
        report['stage'] = 'market_listing'
        deadline = time.monotonic() + 30
        def bounded_page(page, limit, timeout):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Listing deadline exceeded')
            try:
                return fetch_page(page, limit, min(timeout, remaining))
            except HTTPError as exc:
                # Record only locally known request context and the numeric status.
                # The exception URL, headers, reason and response body can contain secrets.
                report['http_status'] = exc.code if type(exc.code) is int else None
                report['http_page'] = page
                report['endpoint_path'] = MARKET_LISTING_PATH
                raise
        if targeted_market is not None:
            if listing_file is not None:
                raise ValueError('Targeted market and complete listing are mutually exclusive')
            from student2.agents.limitless_targeted_discovery import validate_selected
            validate_selected(targeted_market, datetime.now(timezone.utc))
            markets = [targeted_market]
            report['listing_source'] = 'bounded_targeted_discovery'
            report['limitations'].append(
                'The bounded market search is not exhaustive; discovery time does not establish quote freshness.')
        elif listing_file is None:
            markets = _fetch_markets(bounded_page, timeout=10, max_pages=20)
            report['listing_source'] = 'python_direct'
            report['listing_received_at_utc'] = datetime.now(timezone.utc).isoformat()
        else:
            markets, metadata = _load_saved_listing(listing_file)
            report.update(metadata)
            report['limitations'].append(
                'Saved listing provenance and retrieval timestamps are not independently authenticated; '
                'they do not establish quote freshness.')
        report['stage'] = 'market_selection'
        market, start, end, tie = choose_daily(markets, datetime.now(timezone.utc), slug)
        report.update(market=market, market_start=start.isoformat(), market_end=end.isoformat(),
                      market_tie_rule=tie, price_source=SUPPORTED_SOURCE,
                      reference_price_definition=SUPPORTED_REFERENCE)
        report['stage'] = 'book_stream'
        quote, event = capture(market)
        # Do not accept a quote after the market ended during network operations.
        if _datetime(quote['received_at']) >= end:
            raise ValueError('Market ended during collection')
        if read_book_event(event, market, quote['received_at']) != quote:
            raise ValueError('Stream quote and source event disagree')
        report.update(quote=quote, raw_book_event=event, observation_status='book_observed')
        report['stage'] = 'forecast_compatibility'
        if contract is not None:
            result = assess_capture(market, quote, event, contract)
            report['quote_inspection'] = {'event_matches': result.data_status == 'sufficient_data',
                'yes_payout_price': result.probability_up, 'explanation': result.explanation,
                'limitations': result.limitations}
            report['forecast_blocker'] = 'A market payout price is not an evidence-based forecast; use the Betting Odds adapter.'
        else:
            report['forecast_blocker'] = 'No explicit team forecast contract supplied; market odds are displayed only.'
    except Exception as exc:
        # Never leak arbitrary server/credential text. Missing optional dependency is actionable.
        report['observation_status'] = 'failed'
        report['error_type'] = type(exc).__name__
        report['error'] = ('Install python-socketio[client] to enable stream capture'
                           if isinstance(exc, RuntimeError) and 'Install python-socketio' in str(exc)
                           else 'No usable live observation; inspect connectivity, supported rules and stream availability.')
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description='Observe real daily-market odds; never pretend they match a different target.')
    parser.add_argument('--slug')
    parser.add_argument('--listing-file', type=Path,
                        help='Complete listing saved by the PowerShell collector; prices still require a live stream')
    parser.add_argument('--out', type=Path, default=Path('runs/betting_odds_live'))
    for name in ('prediction-time','target-time','price-source','reference-price-definition','tie-definition'):
        parser.add_argument('--'+name)
    args = parser.parse_args()
    names = ('prediction_time','target_time','price_source','reference_price_definition','tie_definition')
    values = {k: getattr(args, k) for k in names}
    if any(values.values()) and not all(values.values()):
        parser.error('Supply all five forecast-contract fields, or none for observation only')
    if all(values.values()):
        try:
            _datetime(values['prediction_time']); _datetime(values['target_time'])
        except (ValueError, TypeError):
            parser.error('Forecast timestamps must be valid and timezone-aware')
    report = observe_market(slug=args.slug, contract=values if all(values.values()) else None,
                            listing_file=args.listing_file)
    run_dir = args.out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10])
    run_dir.mkdir(parents=True, exist_ok=False)
    path = run_dir / 'observation.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('LIVE MARKET OBSERVATION — not automatically a team forecast')
    print('Status:', report['observation_status'], '| Stage:', report.get('stage'))
    if 'quote' in report:
        q = report['quote']
        print('Market:', report['market']['title'])
        print('Window:', report['market_start'], 'to', report['market_end'])
        print('Tie rule:', report['market_tie_rule'])
        print('YES payout midpoint:', format(q['yes_payout_midpoint'], '.2%'))
        print('Best bid / ask:', q['best_bid'], '/', q['best_ask'])
        print('Provider event time:', q['provider_event_time'])
        print('Displayed bid / ask shares:', q['displayed_bid_shares'], '/', q['displayed_ask_shares'])
    print('Team probability_up:', report['team_probability_up'])
    print('Saved:', path.resolve())
    if 'error' in report:
        print(report['error_type']+': '+report['error'])
        if 'http_status' in report:
            print('HTTP status:', report['http_status'], '| Page:', report['http_page'],
                  '| Endpoint:', report['endpoint_path'])
    return 0 if report['observation_status'] == 'book_observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

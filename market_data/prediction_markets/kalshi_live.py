"""Read-only evidence from recent trades on Kalshi's daily Bitcoin series."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from market_data.http import get_json

API = 'https://api.elections.kalshi.com/trade-api/v2'
MAX_TRADE_AGE = timedelta(seconds=30)


def _utc(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('Kalshi timestamp must include a timezone')
    return stamp.astimezone(timezone.utc)


def _number(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid Kalshi number') from exc
    if not number.is_finite():
        raise ValueError('Invalid Kalshi number')
    return number


def market_terms(market):
    ticker = market['ticker']
    event = market['event_ticker']
    if (not re.fullmatch(r'KXBTCD-[A-Z0-9]+', event)
            or not re.fullmatch(re.escape(event) + r'-T[0-9]+(?:\.[0-9]+)?', ticker)
            or market['status'] != 'active' or market['strike_type'] != 'greater'):
        raise ValueError('Unsupported Kalshi daily Bitcoin market')
    end = _utc(market['close_time'])
    local = end.astimezone(ZoneInfo('America/New_York'))
    if (local.hour, local.minute, local.second, local.microsecond) != (17, 0, 0, 0):
        raise ValueError('Unsupported Kalshi fixing time')
    rules = re.fullmatch(
        r"If the simple average of the sixty seconds of CF Benchmarks(?:'|’)? Bitcoin Real-Time Index \(BRTI\) "
        r"before 5 PM (?:EST|EDT|ET) is above ([0-9]+(?:\.[0-9]+)?) at 5 PM (?:EST|EDT|ET) on "
        r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), then the market resolves to Yes\.",
        market['rules_primary'])
    level = _number(market['floor_strike'])
    if (not rules or rules.group(2) != f'{local:%b}'
            or int(rules.group(3)) != local.day or int(rules.group(4)) != local.year
            or level <= 0 or _number(rules.group(1)) != level
            or _number(ticker.rsplit('-T', 1)[1]) != level):
        raise ValueError('Kalshi threshold or fixing date conflicts with its rules')
    return ticker, end, level


def trade_quote(market, trade, received_at):
    ticker, end, _ = market_terms(market)
    received = _utc(received_at)
    stamp = _utc(trade['created_time'])
    yes, no, size = map(_number, (trade['yes_price_dollars'], trade['no_price_dollars'], trade['count_fp']))
    if (trade['ticker'] != ticker or not trade.get('trade_id') or trade.get('is_block_trade') is not False
            or not timedelta(0) <= received - stamp <= MAX_TRADE_AGE or received >= end
            or not 0 < yes < 1 or yes + no != 1 or size <= 0):
        raise ValueError('No fresh, eligible trade for the Kalshi BTC contract')
    return {'received_at': received.isoformat(), 'provider_event_time': stamp.isoformat(),
            'yes_trade_price': float(yes), 'trade_contracts': float(size)}


def observe_bitcoin_trade(*, get=get_json, clock=lambda: datetime.now(timezone.utc)):
    def fetch(path, **params):
        return get(API + path + '?' + urlencode(params), timeout=5)

    candidates = {}
    for market in fetch('/markets', series_ticker='KXBTCD', status='open', limit=100)['markets']:
        try:
            ticker, end, _ = market_terms(market)
            bid, ask = map(_number, (market['yes_bid_dollars'], market['yes_ask_dollars']))
            if end > clock() and 0 < bid <= ask < 1:
                candidates[ticker] = (end, abs((bid + ask) / 2 - Decimal('0.5')), market)
        except (KeyError, TypeError, ValueError):
            continue
    cursor = ''
    min_ts = int((clock() - MAX_TRADE_AGE).timestamp())
    for _ in range(2 if candidates else 0):
        payload = fetch('/markets/trades', limit=1000, min_ts=min_ts, is_block_trade='false', cursor=cursor)
        received = clock().isoformat()
        eligible = []
        for trade in payload['trades']:
            try:
                end, distance, market = candidates[trade['ticker']]
                quote = trade_quote(market, trade, received)
            except (KeyError, TypeError, ValueError):
                continue
            eligible.append((end, distance, -_utc(quote['provider_event_time']).timestamp(), market, trade, quote))
        if eligible:
            _, _, _, market, trade, quote = min(eligible, key=lambda candidate: candidate[:3])
            return {'provider': 'kalshi', 'mode': 'live_read_only_observation',
                    'observation_status': 'trade_observed', 'market': market,
                    'raw_trade': trade, 'quote': quote}
        cursor = payload.get('cursor')
        if not cursor:
            break
    return {'provider': 'kalshi', 'observation_status': 'failed', 'stage': 'recent_trades'}

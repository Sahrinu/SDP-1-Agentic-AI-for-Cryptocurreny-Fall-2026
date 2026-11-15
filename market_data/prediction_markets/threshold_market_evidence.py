"""Validate one explicitly documented Binance BTC threshold-group rule format.

This is evidence about a threshold event, never an equivalent Up probability.
Unknown rule formats or unverified parent/child and book mappings are rejected.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agents.specialists.betting_odds_specialist import _datetime
from market_data.prediction_markets.limitless_live import _number


_RULE = ('This market will immediately resolve to "Yes" if any Binance 1-minute candle for BTC/USDT '
    'during the date range specified in the title (from 12:00 AM ET on the first date to 11:59 PM ET '
    'on the last) has a final "High" price equal to or greater than the price specified in the title. '
    'Otherwise, this market will resolve to "No". The resolution source for this market is Binance, '
    'specifically the BTC/USDT "High" prices available at https://www.binance.com/en/trade/BTC_USDT , '
    'with the chart settings on "1m" candles selected on the top bar. Please note that the outcome of '
    'this market depends solely on the price data from the Binance BTC/USDT trading pair. Prices from '
    'other exchanges, different trading pairs, or spot markets will not be considered for the resolution of this market.')


def validate_threshold(market, parent, prediction):
    """Validate the saved group, child and exact rules; return the threshold and window."""
    if not isinstance(market,dict) or not isinstance(parent,dict):
        raise ValueError('Threshold market and parent group are required')
    if (market.get('tradeType') != 'clob' or market.get('marketType') != 'group'
            or market.get('automationType') != 'manual' or parent.get('marketType') != 'group'
            or parent.get('outcomeTokens') != ['Yes','No']):
        raise ValueError('Unsupported threshold outcome mapping')
    identity=market.get('id'); group=parent.get('id')
    if type(identity) is not int or type(group) is not int or market.get('groupId') != group:
        raise ValueError('Threshold parent and child identities do not agree')
    members=parent.get('markets')
    if not isinstance(members,list):raise ValueError('Threshold parent lacks child markets')
    matches=[m for m in members if isinstance(m,dict) and m.get('id') == identity]
    keys=('id','groupId','slug','title','description','expirationTimestamp','tokens','tradeType','marketType','automationType','status','expired','hidden')
    if len(matches)!=1 or any(matches[0].get(k)!=market.get(k) for k in keys):
        raise ValueError('Threshold child conflicts with its parent group')
    tokens=market.get('tokens')
    if (not isinstance(market.get('slug'),str) or not market['slug'].strip()
            or not isinstance(tokens,dict)
            or not all(isinstance(tokens.get(k),str) and tokens[k].strip() for k in ('yes','no'))
            or tokens['yes']==tokens['no']):
        raise ValueError('Threshold token mapping is not established')
    for other in members:
        if not isinstance(other,dict) or other.get('id') == identity:
            continue
        other_tokens=other.get('tokens')
        if other.get('slug') == market['slug'] or (isinstance(other_tokens,dict) and
                {str(tokens['yes']),str(tokens['no'])} & {str(other_tokens.get('yes')),str(other_tokens.get('no'))}):
            raise ValueError('Duplicate threshold slug or token mapping in parent group')
    for value in (market,parent,matches[0]):
        if value.get('status')!='FUNDED' or value.get('expired') is not False or value.get('hidden') is True:
            raise ValueError('Threshold market or parent is inactive or expired')
        oracle=value.get('priceOracleMetadata')
        if isinstance(oracle,dict) and (oracle.get('ticker') not in (None,'BTCUSDT') or oracle.get('chartSource') not in (None,'binance')):
            raise ValueError('Threshold oracle metadata conflicts with Binance BTC/USDT')
        metadata=value.get('metadata')
        chart=metadata.get('chart') if isinstance(metadata,dict) else None
        if isinstance(chart,dict) and (chart.get('symbol') not in (None,'BTCUSDT') or chart.get('source') not in (None,'binance')):
            raise ValueError('Threshold chart metadata conflicts with Binance BTC/USDT')
        if value.get('createdAt') is not None and _datetime(value['createdAt']) > _datetime(prediction):
            raise ValueError('Threshold market or group was created after prediction')
    text=market.get('description')
    if not isinstance(text,str):raise ValueError('Threshold rules missing')
    text=re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',text))).strip()
    if text!=_RULE:raise ValueError('Unsupported or ambiguous threshold resolution rules')
    group_title=parent.get('title'); child_title=market.get('title')
    if not isinstance(group_title,str) or not isinstance(child_title,str):
        raise ValueError('Threshold range and amount are missing')
    match=re.fullmatch(r'What price will Bitcoin hit ([A-Za-z]+) (\d{1,2})-(\d{1,2})\?',group_title)
    amount=re.fullmatch(r'\u2191 ([1-9]\d{0,2}(?:,\d{3})+)',child_title)
    if not match or not amount:raise ValueError('Unsupported threshold range or amount')
    threshold=int(amount.group(1).replace(',',''))
    end=_datetime(market.get('expirationTimestamp'))
    if _datetime(parent.get('expirationTimestamp'))!=end:
        raise ValueError('Threshold parent and child expiration differ')
    eastern=ZoneInfo('America/New_York')
    local_end=end.astimezone(eastern)
    last=local_end.date()-timedelta(days=1)
    month=datetime.strptime(match.group(1),'%B').month
    first_day,last_day=int(match.group(2)),int(match.group(3))
    if (local_end.hour,local_end.minute,local_end.second,local_end.microsecond)!=(0,0,0,0):
        raise ValueError('Threshold expiration is not the end of the stated ET day')
    if last.month!=month or last.day!=last_day or first_day>last_day:
        raise ValueError('Threshold date range conflicts with expiration')
    start=datetime(last.year,month,first_day,tzinfo=eastern).astimezone(end.tzinfo)
    if _datetime(prediction) >= end:
        raise ValueError('Threshold event has ended before prediction')
    return start,end,threshold


def read_threshold_book(event,market,received_at):
    """Validate timestamp, token and two-sided full-book data for a group child."""
    if not isinstance(event,dict) or event.get('marketSlug')!=market['slug']:
        raise ValueError('Book belongs to a different market')
    received=_datetime(received_at); stamp=_datetime(event.get('timestamp'))
    if not 0 <= (received-stamp).total_seconds() <= 30:
        raise ValueError('Book event is stale or in the future')
    version=event.get('version')
    if isinstance(version,bool) or not isinstance(version,int) or version<=0:
        raise ValueError('Invalid book version')
    book=event.get('orderbook')
    if not isinstance(book,dict):raise ValueError('Missing full order book')
    if book.get('tokenId') is None or str(book['tokenId'])!=str(market['tokens']['yes']):
        raise ValueError('Order book token does not match YES')
    parsed={}
    for side in ('bids','asks'):
        levels=book.get(side)
        if not isinstance(levels,list) or not levels:raise ValueError('Two-sided depth required')
        rows=[]
        for level in levels:
            if not isinstance(level,dict):raise ValueError('Malformed price level')
            price,size=_number(level.get('price')),_number(level.get('size'))
            if not 0<=price<=1 or size<=0:raise ValueError('Invalid price or size')
            rows.append((price,size/1_000_000))
        prices=[row[0] for row in rows]
        if prices!=sorted(prices,reverse=side=='bids') or len(set(prices))!=len(prices):
            raise ValueError('Book levels unordered or duplicated')
        parsed[side]=rows
    bid,ask=parsed['bids'][0][0],parsed['asks'][0][0]
    if bid>ask:raise ValueError('Crossed order book')
    return dict(market_slug=market['slug'],provider_event_time=stamp.isoformat(),
        received_at=received.isoformat(),version=version,best_bid=bid,best_ask=ask,
        spread=ask-bid,yes_payout_midpoint=(bid+ask)/2,
        best_bid_shares=parsed['bids'][0][1],best_ask_shares=parsed['asks'][0][1],
        displayed_bid_shares=sum(s for _,s in parsed['bids']),
        displayed_ask_shares=sum(s for _,s in parsed['asks']))

"""Explicit live collection. Retrieval time is never backdated for replay."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
from urllib.parse import urlencode
import pandas as pd
from market_data.bitcoin.binance import parse_candles
from market_data.http import get_json


def now():
    return datetime.now(timezone.utc)


def collect_bitcoin(get=get_json):
    end = now().replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = end - timedelta(days=1100)
    rows = []
    while cursor < end:
        batch = get('https://api.binance.com/api/v3/klines?' + urlencode(dict(
            symbol='BTCUSDT', interval='1d', startTime=int(cursor.timestamp()*1000),
            endTime=int(end.timestamp()*1000)-1, limit=1000)))
        if not batch:
            break
        rows.extend(batch)
        next_cursor = datetime.fromtimestamp(int(batch[-1][0])/1000, timezone.utc) + timedelta(days=1)
        if next_cursor <= cursor:
            raise ValueError('Non-advancing BTC pagination')
        cursor = next_cursor
    frame = pd.DataFrame([{'date':pd.to_datetime(r[0],unit='ms',utc=True),
                           'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),
                           'close':float(r[4]),'volume':float(r[5])} for r in rows])
    if frame.empty:
        raise ValueError('No completed Bitcoin bars')
    return frame.set_index('date').sort_index()


def collect_recent_bitcoin(get=get_json, *, observed_at=None):
    """Capture completed hourly and minute BTCUSDT bars for a live forecast.

    Indexes are actual Binance close timestamps. A return over N intervals needs
    N+1 closes: 25 hourly closes for 24 hours and 61 minute closes for 60 minutes.
    """
    instant = observed_at or now()
    if instant.tzinfo is None:
        raise ValueError('Intraday capture time must include a timezone')
    instant = instant.astimezone(timezone.utc)
    as_of_ms = int(instant.timestamp() * 1000)
    frames = {}
    for key, interval, count, period_ms in (
            ('btc_hourly', '1h', 25, 3_600_000),
            ('btc_minute', '1m', 61, 60_000)):
        url = 'https://api.binance.com/api/v3/klines?' + urlencode({
            'symbol': 'BTCUSDT', 'interval': interval, 'limit': count + 2})
        candles = parse_candles(get(url), as_of_ms=as_of_ms)[-count:]
        if len(candles) != count or any(
                b.open_time_ms - a.open_time_ms != period_ms
                for a, b in zip(candles, candles[1:])):
            raise ValueError(f'Need {count} consecutive completed {interval} BTC candles')
        frame = pd.DataFrame([{
            'close_time': pd.to_datetime(c.close_time_ms, unit='ms', utc=True),
            'open': float(c.open), 'high': float(c.high), 'low': float(c.low),
            'close': float(c.close), 'volume': float(c.volume_btc),
        } for c in candles]).set_index('close_time')
        if (instant - frame.index[-1]).total_seconds() > (period_ms / 1000 + 120):
            raise ValueError(f'Latest completed {interval} BTC candle is stale')
        frames[key] = frame
    return frames


def collect_chain(get=get_json):
    start = (now() - timedelta(days=20)).date().isoformat()
    raw = get('https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?' + urlencode(dict(
        assets='btc', metrics='AdrActCnt,TxCnt,HashRate,FeeTotNtv',frequency='1d',start_time=start,page_size=1000)))
    receipt = now().isoformat()
    records = raw.get('data', [])
    result = {}
    for family, fields in {'network':{'AdrActCnt':'active_addresses','TxCnt':'transactions'},
                           'miner':{'HashRate':'hash_rate','FeeTotNtv':'fees_btc'}}.items():
        rows = []
        for record in records:
            stamp = pd.Timestamp(record['time'])
            if stamp + pd.Timedelta(days=1) > pd.Timestamp(receipt):
                continue
            if any(record.get(k) is None for k in fields):
                continue
            rows.append({'date':stamp, **{v:float(record[k]) for k,v in fields.items()},
                         'available_at':receipt,'complete':True,'source':'Coin Metrics community API; retrieved vintage'})
        if rows:
            result[family] = pd.DataFrame(rows).set_index('date')
    return result


def reference_quote(get=get_json):
    raw = get('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=3')
    receipt = now()
    completed = [r for r in raw if int(r[6]) < receipt.timestamp()*1000]
    if not completed:
        raise ValueError('No completed one-minute BTC reference bar')
    bar = completed[-1]
    price = float(bar[4])
    if not 0 < price < float('inf'):
        raise ValueError('Invalid reference price')
    return {'price':price,'bar_close_time':pd.to_datetime(bar[6],unit='ms',utc=True).isoformat(),
            'retrieved_at':receipt.isoformat(),'source':'Binance BTCUSDT completed one-minute close'}


def target_quote(target_time, get=get_json):
    """Settlement price: last completed one-minute close at or before target_time.

    Same definition as reference_quote, so a live forecast is settled the way it
    was defined. Used by exchange.ledger.LiveLedger after the 24h window ends.
    """
    if target_time.tzinfo is None:
        raise ValueError('Aware target time required')
    target_ms = int(target_time.timestamp() * 1000)
    if target_ms > now().timestamp() * 1000:
        raise ValueError('Target time has not passed yet')
    raw = get('https://api.binance.com/api/v3/klines?' + urlencode({
        'symbol':'BTCUSDT','interval':'1m','endTime':target_ms,'limit':5}))
    completed = [r for r in raw if int(r[6]) <= target_ms]
    if not completed:
        raise ValueError('No completed one-minute BTC bar at the target time')
    bar = completed[-1]
    price = float(bar[4])
    if not 0 < price < float('inf'):
        raise ValueError('Invalid target price')
    return {'price':price,'bar_close_time':pd.to_datetime(bar[6],unit='ms',utc=True).isoformat(),
            'retrieved_at':now().isoformat(),'source':'Binance BTCUSDT completed one-minute close'}


def collect_news():
    """Fetch the current BTC news feed for a dashboard refresh or forecast."""
    from agents.specialists.news_specialist import _fetch_recent_btc_news
    return _fetch_recent_btc_news()


def collect_betting_inputs(directory, *, observe=None):
    """One read-only stream attempt; persist the exact frame or failure report.

    Call immediately before choosing a new prediction timestamp. REST listing
    prices and historical fixtures are never substituted for a fresh book.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = None
    try:
        from market_data.prediction_markets.limitless_live import observe_current_market
        from agents.specialists.market_evidence_specialist import prepare_evidence
        report = (observe or observe_current_market)()
        receipt = now()
        if report.get('observation_status') != 'book_observed':
            raise ValueError('No qualifying public book received')
        prepare_evidence(report, receipt, receipt + timedelta(hours=24),
            price_source='binance:BTC/USDT',
            reference_price_definition='last completed UTC one-minute close at or before each contract timestamp')
        status = {'status': 'collected', 'received_at': receipt.isoformat(),
                  'provider_event_time': report['quote']['provider_event_time'],
                  'market_slug': report['market']['slug']}
        inputs = {'student2_market_observation': report,
                  'student2_markets_observed_at': receipt.isoformat()}
    except Exception as exc:
        status = {'status': 'unavailable', 'reason': type(exc).__name__,
                  'detail': 'No validated fresh two-sided book; Betting Odds will abstain.'}
        inputs = {}
        if not isinstance(report, dict):
            report = {'observation_status': 'failed', 'error_type': type(exc).__name__}
    (directory / 'betting_observation.json').write_text(json.dumps(report, default=str, indent=2))
    (directory / 'betting_status.json').write_text(json.dumps(status, indent=2))
    return inputs, status


def refresh_betting_inputs(data, directory, *, observe=None):
    """Replace the previous book, including when the new capture fails."""
    for key in ('student2_market_observation', 'student2_markets_observed_at', 'student2_price_context'):
        data.pop(key, None)
    inputs, status = collect_betting_inputs(directory, observe=observe)
    data.update(inputs)
    return status


def collect_snapshot(directory, *, input_directory=None):
    """Independent failures are recorded; unavailable sources never become synthetic."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data, statuses = {}, {}
    def attempt(name, fn):
        try:
            value = fn()
            statuses[name] = {'status':'collected','received_at':now().isoformat()}
            return value
        except Exception as exc:
            statuses[name] = {'status':'unavailable','reason':type(exc).__name__}
            return None
    btc = attempt('btc_prices', collect_bitcoin)
    if btc is not None:
        data['btc_prices'] = btc
    chain = attempt('chain', collect_chain)
    if chain:
        data.update(chain)
    def macro():
        from market_data.macro import collect
        end = now().date() - timedelta(days=1)
        frame = collect(end-timedelta(days=60), end, output=directory/'macro.csv', clock=now)
        # Use the completed download time for the whole retrieved vintage; it is
        # not a provider publication timestamp or historical availability proof.
        receipts = frame['retrieval_completed_at'].dropna().unique()
        if len(receipts) != 1:
            raise ValueError('Macro retrieval completion timestamp is missing or inconsistent')
        receipt = pd.Timestamp(receipts[0])
        if receipt.tzinfo is None:
            raise ValueError('Macro retrieval completion timestamp must be timezone-aware')
        for field in ('dxy','treasury_10y'):
            frame[field+'_available_at'] = receipt.tz_convert('UTC').isoformat()
        frame['availability_basis'] = 'retrieved_vintage_live_only'
        return frame
    value = attempt('macro', macro)
    if value is not None:
        data['macro'] = value
    value = attempt('news', collect_news)
    if value:
        data['student2_news_articles'] = value
        data['student2_news_observed_at'] = now().isoformat()
    def mood():
        from agents.specialists.mood_specialist import parse_fng_response
        return parse_fng_response(get_json('https://api.alternative.me/fng/?limit=30'), now())
    value = attempt('mood', mood)
    if value:
        data['student2_mood_readings'] = value
        data['student2_mood_observed_at'] = now().isoformat()
    def borrowing():
        from market_data.borrowing import collect, BASE
        end = now().date() - timedelta(days=1)
        frame = collect(end-timedelta(days=12),end,output=directory/'borrowing.csv',
            transport=lambda path,params:get_json(BASE+path+'?'+urlencode(params)))
        return frame.set_index('date')
    value = attempt('borrowing', borrowing)
    if value is not None:
        data['borrowing'] = value
    def stablecoin():
        from market_data.stablecoin.data import StablecoinSnapshot, COINS
        from market_data.stablecoin.history import export_history
        key = os.environ.get('COINGECKO_DEMO_API_KEY')
        if not key:
            raise ValueError('CoinGecko key unavailable')
        params = urlencode({'ids':','.join(COINS),'vs_currencies':'usd','include_market_cap':'true',
            'include_24hr_vol':'true','include_last_updated_at':'true','precision':'full'})
        raw = get_json('https://api.coingecko.com/api/v3/simple/price?'+params,headers={'x-cg-demo-api-key':key})
        snapshot = StablecoinSnapshot.from_payload(raw,observed_at=now(),source='coingecko')
        snapshot.validate_freshness(now())
        archive = directory.parent/'stablecoin_archive'
        archive.mkdir(exist_ok=True)
        snapshot.save(archive/(snapshot.observed_at.strftime('%Y%m%dT%H%M%S%fZ')+'.json'))
        # One latest observed vintage per UTC date; no fabricated yesterday baseline.
        selected = {}
        for path in sorted(archive.glob('*.json')):
            selected[path.name[:8]] = path
        target = directory/'stablecoin.csv'
        export_history(selected.values(),target)
        return pd.read_csv(target,index_col='date')
    value = attempt('stablecoin', stablecoin)
    if value is not None:
        data['stablecoin'] = value
    def etf():
        from market_data.etf import import_flows
        if input_directory is None:
            raise ValueError('Reviewed ETF input not supplied')
        frame = import_flows(Path(input_directory)/'etf_flows.csv',directory/'etf_flows.csv')
        return frame.set_index('date')
    value = attempt('etf_flows',etf)
    if value is not None:
        data['etf_flows'] = value
    statuses['betting_odds'] = {'status':'unavailable',
        'reason':'A fresh public book is requested immediately before Generate 24h forecast; no REST-price substitution'}
    # Persist the exact vintage for provenance; not eligible for old-date replay.
    for key,value in data.items():
        if isinstance(value,pd.DataFrame):
            value.to_csv(directory/(key+'.csv'), index_label='date')
        else:
            (directory/(key+'.json')).write_text(json.dumps(value,default=str,indent=2))
    (directory/'manifest.json').write_text(json.dumps({'mode':'live_retrieved_vintage',
        'captured_at':now().isoformat(),'sources':statuses},indent=2))
    return data,statuses

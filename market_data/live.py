"""Explicit live collection. Retrieval time is never backdated for replay."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import time
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
    """Read public market evidence and persist the source record or failure.

    Call immediately before choosing a new prediction timestamp. REST listing
    prices and historical fixtures are never substituted for a fresh book.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = None
    try:
        from market_data.prediction_markets.kalshi_live import observe_bitcoin_trade
        from agents.specialists.market_evidence_specialist import prepare_evidence
        report = (observe or observe_bitcoin_trade)()
        if observe is None and report.get('stage') == 'recent_trades':
            time.sleep(2)
            report = observe_bitcoin_trade()
        receipt = now()
        if report.get('observation_status') not in {'book_observed', 'trade_observed'}:
            raise ValueError('No qualifying public market evidence received')
        prepare_evidence(report, receipt, receipt + timedelta(hours=24),
            price_source='binance:BTC/USDT',
            reference_price_definition='last completed UTC one-minute close at or before each contract timestamp')
        status = {'status': 'collected', 'received_at': receipt.isoformat(),
                  'provider_event_time': report['quote']['provider_event_time'],
                  'market_slug': report['market'].get('ticker') or report['market']['slug'],
                  'provider': report.get('provider', 'limitless')}
        inputs = {'student2_market_observation': report,
                  'student2_markets_observed_at': receipt.isoformat()}
    except Exception as exc:
        status = {'status': 'unavailable', 'reason': type(exc).__name__,
                  'detail': 'No validated fresh two-sided book; Betting Odds will abstain.'}
        if observe is None:
            status.update(provider='kalshi', reason='No fresh eligible Kalshi BTC trade was available',
                          detail=f'Kalshi collection ended with {type(exc).__name__}.')
        elif isinstance(report, dict):
            if report.get('error_type') == 'InitialBookOnlyError':
                status.update(reason='Limitless sent only its initial snapshot; no fresh book update arrived',
                              detail='Betting Odds abstained while waiting for a verified live update.')
            elif report.get('stage') == 'bounded_market_discovery':
                status['reason'] = 'Limitless market discovery found no validated market'
            elif report.get('stage') == 'book_stream':
                status['reason'] = 'No qualifying fresh Limitless order-book update arrived'
            waited = report.get('book_wait_seconds')
            if isinstance(waited, (int, float)):
                status['book_wait_seconds'] = waited
                status['reason'] += f' (waited {waited:g} seconds)'
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


def coingecko_key(project=None):
    """Environment first, else only COINGECKO_DEMO_API_KEY from the project .env (not exported)."""
    key = os.environ.get('COINGECKO_DEMO_API_KEY')
    if not key and project is not None and (Path(project)/'.env').is_file():
        from dotenv import dotenv_values
        key = dotenv_values(Path(project)/'.env').get('COINGECKO_DEMO_API_KEY')
    return key.strip() if isinstance(key,str) and key.strip() else None


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
        except FileNotFoundError:
            reason = 'Missing reviewed data/etf_flows.csv' if name == 'etf_flows' else 'FileNotFoundError'
            statuses[name] = {'status':'unavailable','reason':reason}
            return None
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
        from market_data.yahoo_dashboard import collect_yahoo
        frame = collect_yahoo('macro', directory)
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
    def news():
        from market_data.yahoo_dashboard import collect_yahoo
        articles = collect_yahoo('news', directory)
        if not articles:
            raise ValueError('Yahoo returned no recent news articles')
        return articles
    value = attempt('news', news)
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
        project = Path(input_directory).resolve().parent if input_directory is not None else None
        key = coingecko_key(project)
        if not key:
            raise ValueError('CoinGecko key unavailable')
        params = urlencode({'ids':','.join(COINS),'vs_currencies':'usd','include_market_cap':'true',
            'include_24hr_vol':'true','include_last_updated_at':'true','precision':'full'})
        raw = get_json('https://api.coingecko.com/api/v3/simple/price?'+params,headers={'x-cg-demo-api-key':key})
        snapshot = StablecoinSnapshot.from_payload(raw,observed_at=now(),source='coingecko')
        snapshot.validate_freshness(now())
        target = directory/'stablecoin.csv'
        if project is not None:
            # Share the scheduled archiver's 15-minute receipts so a snapshot taken
            # ~24h earlier exists; export that comparable pair, not "latest per day".
            from market_data.stablecoin.archive import archive_snapshot
            archive = project/'outputs/live_inputs/stablecoin_archive'
            status = archive_snapshot(snapshot, archive, project/'data/stablecoin.csv')
            archive_info.update(archive_status=status['status'],comparison_hours=status['comparison_hours'])
            pair = [status['baseline'], status['snapshot']] if status['baseline'] else [status['snapshot']]
            export_history(pair,target)
            return pd.read_csv(target,index_col='date')
        archive = directory.parent/'stablecoin_archive'
        archive.mkdir(exist_ok=True)
        snapshot.save(archive/(snapshot.observed_at.strftime('%Y%m%dT%H%M%S%fZ')+'.json'))
        # One latest observed vintage per UTC date; no fabricated yesterday baseline.
        selected = {}
        for path in sorted(archive.glob('*.json')):
            selected[path.name[:8]] = path
        export_history(selected.values(),target)
        return pd.read_csv(target,index_col='date')
    def model_stale(path, version=None):
        if not path.is_file() or (now() - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).days >= 7:
            return True
        return version is not None and json.loads(path.read_text(encoding='utf-8')).get('version') != version
    project_root = Path(input_directory).resolve().parent if input_directory is not None else None
    def stablecoin_history():
        # CoinGecko daily history (365 days, has volume): peg-stress model.
        # Refreshed once per UTC day; model retrained when older than 7 days.
        from market_data.stablecoin import depeg_model as dm
        key = coingecko_key(project_root)
        if not key or project_root is None:
            raise ValueError('CoinGecko key or project unavailable')
        path, model_path = project_root/'data/stablecoin_history.csv', project_root/'data/stablecoin_depeg_model.json'
        frame = dm.load_history(path) if path.is_file() else None
        if frame is None or frame.index[-1].date() < now().date():
            frame = dm.fetch_history(key, get=lambda url, headers=None: get_json(url, headers=headers or {}))
            dm.save_history(frame, path)
        if model_stale(model_path):
            model_path.write_text(json.dumps(dm.train(frame), indent=2)+'\n', encoding='utf-8')
        return frame
    value = attempt('stablecoin_history', stablecoin_history)
    if value is not None:
        data['stablecoin_history'] = value
    def stablecoin_long_history():
        # DefiLlama supply + Coinbase BTC-USD since 2019 (free, no key): liquidity model.
        # Refetched when the saved copy is over 12 hours old; model retrained weekly.
        from market_data.stablecoin import defillama, liquidity_model as lm
        from market_data.stablecoin.depeg_model import load_history
        if project_root is None:
            raise ValueError('Project directory unavailable')
        path = project_root/'data/stablecoin_history_long.csv'
        model_path = project_root/'data/stablecoin_liquidity_model.json'
        fresh = path.is_file() and (now() - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)) < timedelta(hours=12)
        if fresh:
            frame = load_history(path)
        else:
            frame = defillama.fetch_history(get=lambda url, headers=None: get_json(url, timeout=30, headers=headers or {}))
            frame.to_csv(path)
        if model_stale(model_path, version=3):
            model_path.write_text(json.dumps(lm.train(frame), indent=2)+'\n', encoding='utf-8')
        return frame
    value = attempt('stablecoin_long_history', stablecoin_long_history)
    if value is not None:
        data['stablecoin_history_long'] = value
    archive_info = {}
    value = attempt('stablecoin', stablecoin)
    statuses['stablecoin'].update(archive_info)
    if value is not None:
        data['stablecoin'] = value
    elif not coingecko_key(Path(input_directory).resolve().parent if input_directory is not None else None):
        statuses['stablecoin']['reason'] = 'COINGECKO_DEMO_API_KEY is not configured'
    def etf():
        from market_data.etf import import_flows
        if input_directory is None:
            raise ValueError('Reviewed ETF input not supplied')
        source = Path(input_directory)/'etf_flows.csv'
        from market_data import etf_sosovalue
        key = etf_sosovalue.api_key(Path(input_directory).resolve().parent/'.env')
        if key:
            # Keep the file current from SoSoValue (last ~month on the free key);
            # older reviewed rows are kept. A failed refresh falls back to the file.
            try:
                etf_sosovalue.update_csv(source, key, get=lambda url, headers: get_json(url, timeout=30, headers=headers))
                etf_info['etf_refresh'] = 'sosovalue'
            except Exception as exc:
                etf_info['etf_refresh'] = f'failed: {type(exc).__name__}; using saved file'
        frame = import_flows(source,directory/'etf_flows.csv')
        return frame.set_index('date')
    etf_info = {}
    value = attempt('etf_flows',etf)
    statuses['etf_flows'].update(etf_info)
    if value is not None:
        data['etf_flows'] = value
    statuses['betting_odds'] = {'status':'unavailable',
        'reason':'A fresh public Kalshi BTC trade is requested immediately before Generate 24h forecast'}
    # Persist the exact vintage for provenance; not eligible for old-date replay.
    for key,value in data.items():
        if isinstance(value,pd.DataFrame):
            value.to_csv(directory/(key+'.csv'), index_label='date')
        else:
            (directory/(key+'.json')).write_text(json.dumps(value,default=str,indent=2))
    (directory/'manifest.json').write_text(json.dumps({'mode':'live_retrieved_vintage',
        'captured_at':now().isoformat(),'sources':statuses},indent=2))
    return data,statuses

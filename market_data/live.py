"""Explicit live collection. Retrieval time is never backdated for replay."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
from urllib.parse import urlencode
import pandas as pd
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
        frame = collect(end-timedelta(days=60), end, output=directory/'macro.csv')
        # Live receipt, not the historical assumed midnight, determines eligibility.
        for field in ('dxy','treasury_10y'):
            frame[field+'_available_at'] = now().isoformat()
        frame['availability_basis'] = 'retrieved_vintage_live_only'
        return frame
    value = attempt('macro', macro)
    if value is not None:
        data['macro'] = value
    def news():
        from agents.specialists.news_specialist import _fetch_recent_btc_news
        return _fetch_recent_btc_news()
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
        'reason':'Requires validated timestamped market-book capture; no automatic REST-price substitution'}
    # Persist the exact vintage for provenance; not eligible for old-date replay.
    for key,value in data.items():
        if isinstance(value,pd.DataFrame):
            value.to_csv(directory/(key+'.csv'), index_label='date')
        else:
            (directory/(key+'.json')).write_text(json.dumps(value,default=str,indent=2))
    (directory/'manifest.json').write_text(json.dumps({'mode':'live_retrieved_vintage',
        'captured_at':now().isoformat(),'sources':statuses},indent=2))
    return data,statuses

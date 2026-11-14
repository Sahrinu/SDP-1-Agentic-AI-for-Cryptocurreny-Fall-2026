"""Public Binance context for the experimental market-evidence model.
No orders, credentials, retries or automatic paid calls.
"""
import json, math, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from student2.agents.betting_odds_specialist import _datetime, _rules

BASE = 'https://data-api.binance.vision'

def fetch(path, params):
    url=BASE+path+'?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        raw=json.load(r)
    return {'source_url':url, 'received_at':datetime.now(timezone.utc).isoformat(), 'data':raw}

def collect(observation, request=fetch):
    start,_,_=_rules(observation['market'])
    now=int(datetime.now(timezone.utc).timestamp()*1000)
    return {'source':'binance:BTC/USDT',
        'hourly':request('/api/v3/klines',dict(symbol='BTCUSDT',interval='1h',endTime=now,limit=170)),
        'opening':request('/api/v3/klines',dict(symbol='BTCUSDT',interval='1m',startTime=int(start.timestamp()*1000),limit=1)),
        'trade':request('/api/v3/aggTrades',dict(symbol='BTCUSDT',limit=1))}

def number(x):
    if isinstance(x,bool):raise ValueError('Boolean price')
    n=float(x)
    if not math.isfinite(n) or n<=0:raise ValueError('Nonpositive or nonfinite price')
    return n

def decode_rows(value, kind):
    """Decode one transport JSON layer; reject malformed/provider-error bodies.

    Older PowerShell captures may contain either JSON text or [JSON text].
    Do not change the saved artifact, timestamps or numeric validation.
    """
    if isinstance(value,list) and len(value)==1 and isinstance(value[0],str):
        value=value[0]
    if isinstance(value,str):
        if len(value)>2_000_000:raise ValueError('Response text too large')
        try:value=json.loads(value)
        except (ValueError,RecursionError) as exc:raise ValueError('Invalid response JSON') from exc
    if not isinstance(value,list) or not value or len(value)>1000:
        raise ValueError('Expected a nonempty bounded response array')
    if kind=='trade':
        if any(not isinstance(r,dict) or not {'T','p'}<=r.keys() for r in value):
            raise ValueError('Expected trade objects with price and timestamp')
    elif any(not isinstance(r,list) or len(r)<7 for r in value):
        raise ValueError('Expected candle rows')
    return value


def validate(context, observation, prediction):
    prediction=_datetime(prediction); ms=int(prediction.timestamp()*1000)
    if context.get('source')!='binance:BTC/USDT':raise ValueError('Wrong source')
    for key,path in [('hourly','/api/v3/klines'),('opening','/api/v3/klines'),('trade','/api/v3/aggTrades')]:
        entry=context[key]
        if not 0<=(prediction-_datetime(entry['received_at'])).total_seconds()<=120:raise ValueError('Stale or future receipt')
        url=urllib.parse.urlparse(entry['source_url']);q=urllib.parse.parse_qs(url.query)
        if url.scheme!='https' or url.netloc!='data-api.binance.vision' or url.path!=path or q.get('symbol')!=['BTCUSDT']:raise ValueError('Wrong endpoint or pair')
        if key!='trade' and q.get('interval')!=[{'hourly':'1h','opening':'1m'}[key]]:raise ValueError('Wrong candle interval')
    def candles(rows, interval, received):
        out=[]
        for r in rows:
            t=int(r[0]); close=int(r[6])
            if t%interval or close!=t+interval-1:raise ValueError('Invalid candle times')
            o,h,l,c=map(number,r[1:5])
            if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid candle OHLC')
            if close<min(ms,received):out.append((t,close,c))
        if len({x[0] for x in out})!=len(out):raise ValueError('Duplicate candles')
        if any(b[0]-a[0]!=interval for a,b in zip(out,out[1:])):raise ValueError('Missing or unordered candles')
        return out
    rows=candles(decode_rows(context['hourly']['data'],'candles'),3600000,int(_datetime(context['hourly']['received_at']).timestamp()*1000))
    if len(rows)<169:raise ValueError('Need 169 completed hourly candles')
    rows=rows[-169:]
    if not 0<=ms-rows[-1][1]<=3601000:raise ValueError('Hourly history is stale')
    trade=decode_rows(context['trade']['data'],'trade')
    if len(trade)!=1:raise ValueError('Expected one recent trade')
    t=int(trade[0]['T']); spot=number(trade[0]['p'])
    if t>int(_datetime(context['trade']['received_at']).timestamp()*1000) or not 0<=ms-t<=60000:raise ValueError('Latest trade stale or future')
    start,_,_=_rules(observation['market']);expected=int(start.timestamp()*1000)
    opening=candles(decode_rows(context['opening']['data'],'candles'),60000,int(_datetime(context['opening']['received_at']).timestamp()*1000))
    if len(opening)!=1 or opening[0][0]!=expected:raise ValueError('Opening candle unavailable or wrong')
    closes=[r[2] for r in rows]; returns=[math.log(b/a) for a,b in zip(closes,closes[1:])]
    return {'evidence_id':'binance-prices-001','price_source':'binance:BTC/USDT',
        'latest_trade_price':spot,'latest_trade_time':_datetime(t).isoformat(),
        'reference_note':'Latest trade before prediction, up to 60 seconds old; not an exact simultaneous spot fixing.',
        'market_start_candle_close':opening[0][2],
        'market_start_candle_close_time':_datetime(opening[0][1]).isoformat(),
        'change_since_market_reference':spot/opening[0][2]-1,
        'completed_hourly_candles':len(rows),'last_completed_close_time':_datetime(rows[-1][1]).isoformat(),
        'hourly_close_return_24h':closes[-1]/closes[-25]-1,
        'hourly_close_return_7d':closes[-1]/closes[0]-1,
        'realized_volatility_24h':statistics.stdev(returns[-24:])*math.sqrt(24),
        'realized_volatility_7d_scaled_to_24h':statistics.stdev(returns)*math.sqrt(24),
        'volatility_note':'Sample standard deviation of hourly log returns times sqrt(24); historical, not a probability.'}

"""Reproducible paired Brier uncertainty and cost-matched trading baselines."""
import argparse
import json
import math
from pathlib import Path
import random
import pandas as pd
from execution.paper_trading import PaperTrader
from risk.gateway import execute_guarded


def paired_brier(probabilities, outcomes, *, repeats=1000,block_size=7,seed=42):
    if not 100 <= repeats <= 100000 or block_size < 1:
        raise ValueError('Use 100–100000 repeats and positive blocks')
    if len(probabilities)!=len(outcomes):
        raise ValueError('Probabilities and outcomes must align')
    pairs=[]
    for p,y in zip(probabilities,outcomes):
        if pd.isna(p) or pd.isna(y):continue
        if isinstance(p,bool) or not math.isfinite(float(p)) or not 0 <= p <= 1 or y not in (0,1,False,True):
            raise ValueError('Invalid probability or binary outcome')
        pairs.append((float(p)-int(y))**2)
    if len(pairs)<2:
        return {'n':len(pairs),'brier':None,'ci95':None,'reason':'Need at least two settled predictions'}
    rng=random.Random(seed);n=len(pairs);means=[]
    for _ in range(repeats):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n)
            sample.extend(pairs[(start+j)%n] for j in range(min(block_size,n)))
        means.append(sum(sample[:n])/n)
    means.sort()
    return {'n':n,'brier':sum(pairs)/n,'ci95':[means[int(.025*repeats)],means[min(repeats-1,int(.975*repeats))]],
        'bootstrap':'circular moving blocks','block_size':min(block_size,n),'repeats':repeats,'seed':seed,
        'limitation':'Small samples and regime changes can make intervals unreliable'}


def trading_baselines(prices, *, start,end,output,execution='next_open', capped=True):
    """Signals use D close and execute on D+1. SMA has 50 completed-day warmup.

    Both variants use identical fees/slippage. Capped buy-and-hold may be fully
    liquidated by the gateway on price drift, so it is explicitly named a
    capped allocation strategy rather than conventional buy-and-hold.
    """
    if execution not in {'next_open','next_close'}:raise ValueError('Unknown execution convention')
    frame=prices.copy();frame.index=pd.to_datetime(frame.index,utc=True).normalize();frame=frame.sort_index()
    if frame.empty or frame.index.has_duplicates or any((b-a).days!=1 for a,b in zip(frame.index,frame.index[1:])):
        raise ValueError('Need unique consecutive daily candles')
    field='open' if execution=='next_open' else 'close'
    for name in {'close',field}:
        if name not in frame or not all(math.isfinite(float(v)) and float(v)>0 for v in frame[name]):
            raise ValueError('Invalid or missing execution prices')
    first,last=pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC')
    dates=pd.date_range(first,last,tz='UTC')
    if first not in frame.index or last+pd.Timedelta(days=1) not in frame.index or first>last:
        raise ValueError('Need forecast window and next-day execution candle')
    if len(frame.loc[:first])<50:raise ValueError('Need 50 daily closes before first SMA decision')
    short,long=frame.close.rolling(20).mean(),frame.close.rolling(50).mean()
    out=Path(output)
    if out.exists() and any(out.iterdir()):raise FileExistsError('Choose an empty baseline output directory')
    summaries=[]
    for name in ('capped_allocation' if capped else 'buy_and_hold','sma20_50'):
        account=PaperTrader();curve=[]
        for day in dates:
            following=day+pd.Timedelta(days=1);price=float(frame.loc[following,field])
            action=('BUY' if day==first else 'HOLD') if name!='sma20_50' else ('BUY' if short.loc[day]>long.loc[day] else 'SELL')
            stamp=following if execution=='next_open' else following+pd.Timedelta(days=1)
            if capped:execute_guarded(account,action,price,timestamp=stamp.to_pydatetime())
            else:
                # Fully allocated SMA holds until its signal changes; do not buy repeatedly.
                if action=='BUY' and account.btc:action='HOLD'
                account.execute(action,price,timestamp=stamp.to_pydatetime())
            curve.append({'date':str(following.date()),**account.summary(frame.loc[following,'close'])})
        folder=out/name;account.export(folder,frame.loc[last+pd.Timedelta(days=1),'close'])
        pd.DataFrame(curve).to_csv(folder/'equity.csv',index=False)
        summaries.append({'method':name,'capped':capped,**curve[-1]})
    out.mkdir(parents=True,exist_ok=True)
    (out/'run.json').write_text(json.dumps({'execution':execution,'cap':.25 if capped else None,
        'fee':.001,'slippage_bps':5,'timing':'D close signal, D+1 execution',
        'capped_buy_hold':'Single initial buy; gateway may liquidate an overweight position; no reinvestment'},indent=2))
    pd.DataFrame(summaries).to_csv(out/'summary.csv',index=False)
    return summaries


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prices',required=True);p.add_argument('--start',required=True)
    p.add_argument('--end',required=True);p.add_argument('--output',required=True);p.add_argument('--uncapped',action='store_true')
    args=p.parse_args();frame=pd.read_csv(args.prices,index_col='date')
    trading_baselines(frame,start=args.start,end=args.end,output=args.output,capped=not args.uncapped)

if __name__=='__main__':main()

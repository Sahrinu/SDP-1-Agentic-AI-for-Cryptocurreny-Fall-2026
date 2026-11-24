"""Offline fabricated data; no provider or paid calls."""
import sys, unittest, copy, math
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_market_evidence_specialist import observation as old_observation, PRED as OLD_PRED, response
from market_data.prediction_markets.limitless_live import read_book_event
PRED=OLD_PRED+timedelta(hours=2)
def observation():
    o=old_observation();o["raw_book_event"]["timestamp"]=PRED.isoformat()
    o["quote"]=read_book_event(o["raw_book_event"],o["market"],PRED)
    return o
from market_data.prediction_markets import market_price_context as price
from agents.specialists import market_evidence_with_prices as enriched
from agents.specialists.betting_odds_specialist import _rules

def context():
    ms=int(PRED.timestamp()*1000);end=ms//3600000*3600000
    def row(t,period,value):return [t,str(value),str(value+1),str(value-1),str(value), '1',t+period-1]
    start=int(_rules(observation()['market'])[0].timestamp()*1000)
    def entry(path,rows):return dict(source_url=price.BASE+path,received_at=PRED.isoformat(),data=rows)
    return dict(source='binance:BTC/USDT',
        hourly=entry('/api/v3/klines?symbol=BTCUSDT&interval=1h',[row(end-i*3600000,3600000,70000-i) for i in range(169,0,-1)]),
        opening=entry('/api/v3/klines?symbol=BTCUSDT&interval=1m',[row(start,60000,69000)]),
        trade=entry('/api/v3/aggTrades?symbol=BTCUSDT',[{'T':ms-1000,'p':'70000'}]))

class PriceTests(unittest.TestCase):
    def valid(self,c=None):return price.validate(context() if c is None else c,observation(),PRED)
    def test_features(self):
        v=self.valid();self.assertEqual(v['latest_trade_price'],70000)
        self.assertEqual(v['completed_hourly_candles'],169)
        self.assertAlmostEqual(v['hourly_close_return_24h'],69999/69975-1)
        self.assertGreater(v['realized_volatility_24h'],0)
    def test_no_model_during_prepare(self):
        f=enriched.prepare(observation(),context(),PRED)
        self.assertEqual(len(f['evidence']),2)
        self.assertFalse(any(s.startswith('Current spot price, independently') for s in f['limitations']))
    def test_stale_trade(self):
        c=context();c['trade']['data'][0]['T']-=60000
        with self.assertRaises(ValueError):self.valid(c)
    def test_future_trade(self):
        c=context();c['trade']['data'][0]['T']+=2000
        with self.assertRaises(ValueError):self.valid(c)
    def test_future_receipt(self):
        c=context();c['trade']['received_at']=(PRED+timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):self.valid(c)
    def test_wrong_symbol(self):
        c=context();c['trade']['source_url']=c['trade']['source_url'].replace('BTCUSDT','ETHUSDT')
        with self.assertRaises(ValueError):self.valid(c)
    def test_missing_candle(self):
        c=context();c['hourly']['data'].pop(50)
        with self.assertRaises(ValueError):self.valid(c)
    def test_duplicate(self):
        c=context();c['hourly']['data'].append(c['hourly']['data'][-1])
        with self.assertRaises(ValueError):self.valid(c)
    def test_nonfinite(self):
        c=context();c['trade']['data'][0]['p']='NaN'
        with self.assertRaises(ValueError):self.valid(c)
    def test_open_reference(self):
        c=context();c['opening']['data'][0][0]+=60000;c['opening']['data'][0][6]+=60000
        with self.assertRaises(ValueError):self.valid(c)
    def test_incomplete_hour_ignored(self):
        c=context();r=c['hourly']['data'][-1].copy();r[0]+=3600000;r[6]+=3600000;c['hourly']['data'].append(r)
        self.assertEqual(self.valid(c)['completed_hourly_candles'],169)
    def test_citations(self):
        f=enriched.prepare(observation(),context(),PRED)
        with self.assertRaises(ValueError):enriched.evaluate(f,lambda _:{**response(),'probability_up':.55,'evidence_ids':['fake']})
    def test_abstention_preserved(self):
        f=enriched.prepare(observation(),context(),PRED)
        r=enriched.evaluate(f,lambda _:{**response(),'probability_up':None,'data_status':'insufficient_data','evidence_ids':[]})
        self.assertIsNone(r.probability_up)
    def test_numeric_result_is_model_not_quote(self):
        f=enriched.prepare(observation(),context(),PRED)
        r=enriched.evaluate(f,lambda _:{**response(),'probability_up':.55,'evidence_ids':['binance-prices-001']})
        self.assertEqual(r.probability_up,.55)
        self.assertIn('uncalibrated',r.explanation)
if __name__=='__main__':unittest.main()

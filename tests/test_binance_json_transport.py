"""Transport regression tests; no HTTP requests or model calls."""
import unittest,json,copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from student2.agents.market_price_context import decode_rows

class JsonTransportTests(unittest.TestCase):
    def test_native_trade(self):
        rows=[{'T':123,'p':'100'}];self.assertEqual(decode_rows(rows,'trade'),rows)
    def test_text_trade(self):
        rows=[{'T':123,'p':'100'}];self.assertEqual(decode_rows(json.dumps(rows),'trade'),rows)
    def test_wrapped_text_trade(self):
        rows=[{'T':123,'p':'100'}];self.assertEqual(decode_rows([json.dumps(rows)],'trade'),rows)
    def test_candles_native_and_text(self):
        rows=[[0,'1','2','1','2','0',59999]]
        for value in (rows,json.dumps(rows),[json.dumps(rows)]):
            with self.subTest(value=value):self.assertEqual(decode_rows(value,'candles'),rows)
    def test_no_mutation(self):
        rows=['[{"T":123,"p":"100"}]'];before=copy.deepcopy(rows)
        decode_rows(rows,'trade');self.assertEqual(rows,before)
    def test_error_shapes_rejected(self):
        for value in ('not JSON','<html>error</html>','{"code":-1}',{},None,42,[],['not JSON'],'["not a trade"]','[{"p":"1"}]',json.dumps(json.dumps([{'T':123,'p':'100'}]))):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):decode_rows(value,'trade')
    def test_flattened_candle_rejected(self):
        with self.assertRaises(ValueError):decode_rows([0,'1','2','1','2','0',59999],'candles')
    def test_bound(self):
        with self.assertRaises(ValueError):decode_rows([{'T':1,'p':'1'}]*1001,'trade')
if __name__=='__main__':unittest.main()

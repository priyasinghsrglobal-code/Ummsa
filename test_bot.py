import unittest, copy
from unittest.mock import patch
from datetime import datetime, timezone
import engine

NOW=1800000300

def payload():
    rows=[]
    for i in range(81):
        t=NOW-(81-i)*300
        p=100+i*.1
        rows.append(dict(datetime=datetime.fromtimestamp(t,timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),open=str(p),high=str(p+1),low=str(p-1),close=str(p+.2)))
    return {'meta':{'symbol':'EUR/USD','interval':'5min'},'values':rows}

class EngineTests(unittest.TestCase):
    def test_valid(self):
        b=engine.validate(payload(),'EURUSD','5m',NOW)
        self.assertEqual(len(b),81)
    def test_open_bar_removed(self):
        p=payload();p['values'][-1]['datetime']=datetime.fromtimestamp(NOW-30,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.assertEqual(len(engine.validate(p,'EURUSD','5m',NOW)),80)
    def test_missing(self):
        with self.assertRaises(engine.DataError):engine.validate({'status':'error'},'EURUSD','5m',NOW)
    def test_stale(self):
        with self.assertRaisesRegex(engine.DataError,'stale'):engine.validate(payload(),'EURUSD','5m',NOW+1000)
    def test_nan(self):
        p=payload();p['values'][3]['high']='NaN'
        with self.assertRaises(engine.DataError):engine.validate(p,'EURUSD','5m',NOW)
    def test_duplicate(self):
        p=payload();p['values'].append(p['values'][-1])
        with self.assertRaises(engine.DataError):engine.validate(p,'EURUSD','5m',NOW)
    def test_gap(self):
        p=payload();p['values'].pop(30)
        with self.assertRaisesRegex(engine.DataError,'gap'):engine.validate(p,'EURUSD','5m',NOW)
    def test_insufficient(self):
        p=payload();p['values']=p['values'][-10:]
        with self.assertRaises(engine.DataError):engine.validate(p,'EURUSD','5m',NOW)
    def test_wrong_symbol(self):
        with self.assertRaises(engine.DataError):engine.validate(payload(),'XAUUSD','5m',NOW)
    def test_wrong_timeframe(self):
        with self.assertRaises(engine.DataError):engine.validate(payload(),'EURUSD','1h',NOW)
    def test_bad_ohlc(self):
        p=payload();p['values'][4]['low']='200'
        with self.assertRaises(engine.DataError):engine.validate(p,'EURUSD','5m',NOW)
    def test_sweep(self):
        b=engine.validate(payload(),'EURUSD','5m',NOW);r=max(x['h'] for x in b[-21:-1]);b[-1].update(h=r+1,c=r-.2)
        a=engine.analyze(b,'SMC')
        self.assertTrue(any('Buy-side sweep' in s for s in a['facts']))
        self.assertFalse(any('sweep' in s for s in engine.analyze(b,'Price Action')['facts']))
    def test_report(self):
        text=engine.report(engine.validate(payload(),'EURUSD','5m',NOW),'EURUSD','5m','SMC')
        for x in ['Trend:','Support:','Resistance:','Bullish scenario:','Bearish scenario:','Invalidated','UTC','Twelve Data']:
            self.assertIn(x,text)
    def test_chart(self):
        from chart import render
        result=render(engine.validate(payload(),'EURUSD','5m',NOW),'EURUSD','5m','SMC')
        self.assertTrue(result.startswith(b'\x89PNG'))
    def test_callback_flow(self):
        import bot
        msg={'chat':{'id':1,'type':'private'}}
        with patch.object(bot,'telegram') as tg,patch.object(bot,'send') as send:
            bot.handle({'message':msg});self.assertIn('Select a symbol',send.call_args.args[1])
            bot.handle({'callback_query':{'id':'1','message':msg,'data':'s:EURUSD'}})
            self.assertEqual(send.call_args.args[1],'Select timeframe')
            bot.handle({'callback_query':{'id':'2','message':msg,'data':'t:EURUSD:5m'}})
            self.assertEqual(send.call_args.args[1],'Select analysis method')
    def test_failure_never_charts(self):
        import bot
        bot.cooldowns.clear()
        with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'candles',side_effect=engine.DataError('Data unavailable')),patch.object(bot,'photo') as photo:
            bot.handle({'callback_query':{'id':'3','message':{'chat':{'id':5,'type':'private'}},'data':'a:EURUSD:5m:SMC'}})
            photo.assert_not_called();self.assertEqual(send.call_args.args[1],'Data unavailable')

if __name__=='__main__': unittest.main()

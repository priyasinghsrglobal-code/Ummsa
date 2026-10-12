import math, unittest, tempfile, os
from unittest.mock import patch
import engine, bot

def bars(direction=1, count=300):
    result=[]
    for n in range(count):
        c=4200+direction*(n*.6+4*math.sin(n*.5))
        result.append(dict(t=1700000000+n*3600,o=c-.2*direction,h=c+1,l=c-1,c=c))
    return result

class DetailedAnalysis(unittest.TestCase):
    def test_reference_rr_is_not_uniform(self):
        self.assertAlmostEqual(engine.reward_risk(4179.4,4147.5,4195),.489028,places=5)
        self.assertAlmostEqual(engine.reward_risk(4179.4,4147.5,4219.42),1.254545,places=5)
        self.assertAlmostEqual(engine.reward_risk(4179.4,4147.5,4245),2.056426,places=5)
        self.assertIsNone(engine.reward_risk(100,100,105))
    def test_flat_indicators_no_trade(self):
        data=[dict(t=n*3600,o=100,h=100,l=100,c=100) for n in range(300)]
        i=engine.indicators(data)
        self.assertEqual(i['rsi'],50);self.assertEqual(i['macd'],0);self.assertEqual(i['atr'],0)
        self.assertIsNone(engine.market_setup(data,'SMC')[-1])
        self.assertIn('NO TRADE PLAN',engine.detailed_report(data,'XAUUSD','1h','SMC'))
    def test_warmup_and_monotonic_rsi(self):
        self.assertIsNone(engine.indicators(bars(count=60))['ema200'])
        with self.assertRaises(engine.DataError):engine.indicators(bars(count=20))
        up=[dict(t=n,o=n+100,h=n+101,l=n+99,c=n+100) for n in range(300)]
        self.assertEqual(engine.indicators(up)['rsi'],100)
        self.assertAlmostEqual(engine.indicators(up)['atr'],2)
        self.assertEqual(engine.ema_series([1,2,3,4],3),[None,None,2,3])
    def test_long_short_math_and_length(self):
        for direction in (1,-1):
            a,i,d,score,setup=engine.market_setup(bars(direction),'SMC')
            self.assertEqual(d,direction)
            self.assertIsNotNone(setup)
            for n,t in enumerate(setup['targets'],1):
                self.assertAlmostEqual(engine.reward_risk(setup['entry'],setup['stop'],t),n)
            for sym in engine.SYMBOLS:
                text=engine.detailed_report(bars(direction),sym,'1h','SMC')
                self.assertLess(len(text.encode('utf-16-le'))//2,4096)
                self.assertIn('not a win probability',text)
                self.assertIn('Candle closed:',text)
    def test_detailed_delivery_uses_no_ai_or_chart(self):
        state=dict(selection={},history=[],preferences={})
        with patch('bot.session',return_value=state),patch('bot.candles',return_value=bars()),patch('bot.route') as ai,patch('bot.photo') as photo,patch('bot.send') as send,patch('features.feedback_buttons',return_value=[]):
            bot.analyze_for_chat(1,'XAUUSD','1h','SMC','Gold full analysis')
            ai.assert_not_called();photo.assert_not_called()
            self.assertIn('TP1:',send.call_args.args[1])
    def test_missing_data_withholds_plan(self):
        with patch('bot.session',return_value=dict(selection={},history=[])),patch('bot.candles',side_effect=engine.DataError('Data stale')),patch('bot.send') as send:
            bot.analyze_for_chat(1,'XAUUSD','1h','SMC')
            self.assertEqual(send.call_args.args[1],'Data stale')
    def test_modes_and_method(self):
        self.assertTrue(bot.full_analysis_requested('gold analysis'))
        self.assertFalse(bot.full_analysis_requested('gold short analysis'))
        self.assertFalse(bot.full_analysis_requested('current gold price'))
        state=dict(selection={},pending=None)
        bot.direct_market('gold 1h SMC analysis',state)
        self.assertEqual(state['selection']['method'],'SMC')

if __name__=='__main__':unittest.main()

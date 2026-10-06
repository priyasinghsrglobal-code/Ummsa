import unittest, tempfile, os
from unittest.mock import patch
import bot,mentor
from engine import DataError
class ConversationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'BOT_DB_PATH':self.tmp.name+'/test.db'});self.env.start();bot.sessions.clear();bot.cooldowns.clear()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def message(self,text):return {'message':{'from':{'id':77},'text':text,'chat':{'id':77,'type':'private'}}}
 def test_greeting(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',return_value={'action':'chat','reply':'Hi 👋 How can I help?','symbol':None,'timeframe':None,'method':None}):
   bot.handle(self.message('Hello'));self.assertEqual(send.call_args.args[1],'Hi 👋 How can I help?')
 def test_context(self):
  bot.session(77)['selection']={'symbol':'XAUUSD','timeframe':'15m','method':'SMC'}
  with patch.object(bot,'telegram'),patch.object(bot,'route',return_value={'action':'analyze','reply':'','symbol':None,'timeframe':None,'method':None}),patch.object(bot,'analyze_for_chat') as analyze:
   bot.handle(self.message('abhi kya scene hai?'));analyze.assert_called_once_with(77,'XAUUSD','15m','SMC',question='abhi kya scene hai?')
 def test_missing_tf(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',return_value={'action':'analyze','reply':'','symbol':'XAUUSD','timeframe':None,'method':None}),patch.object(bot,'analyze_for_chat') as analyze:
   bot.handle(self.message('Gold?'));analyze.assert_not_called();self.assertIn('timeframe',send.call_args.args[1])
 def test_ai_unavailable(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',side_effect=DataError('quota unavailable')):
   bot.handle(self.message('explain candle'));self.assertIn('quota unavailable',send.call_args.args[1])
 def test_reset(self):
  bot.session(77)['history']=[{'text':'old'}]
  with patch.object(bot,'telegram'),patch.object(bot,'send'):bot.handle(self.message('/reset'))
  self.assertEqual(bot.session(77)['history'],[])
 def test_attachment_reply(self):
  with patch.object(bot,'send') as send:bot.handle(self.message(''))
  self.assertIn('text',send.call_args.args[1])
 def test_no_chart_key(self):
  with patch.object(mentor,'CHART_KEY',''):
   with self.assertRaises(DataError):mentor.snapshot('XAUUSD','5m')

class ChartIntentTests(unittest.TestCase):
 def test_explicit_only(self):
  for text in ['Gold ka chart dikhao','show EURUSD chart','chart bhejo']:
   self.assertTrue(bot.wants_chart(text))
  for text in ['Gold ka analysis','entry lu?','aisa kyun?','chart kya hai?','chart mat bhejo','no chart please']:
   self.assertFalse(bot.wants_chart(text))
 def test_analysis_no_photo(self):
  from test_bot import payload,NOW
  from engine import validate
  with patch.object(bot,'candles',return_value=validate(payload(),'EURUSD','5m',NOW)),patch.object(bot,'route',return_value={'reply':'Range mein hai; confirmation ka wait karo.'}),patch.object(bot,'send'),patch.object(bot,'photo') as photo,patch.object(bot,'snapshot') as snapshot:
   bot.analyze_for_chat(88,'EURUSD','5m','SMC',question='Kya scene hai?')
   photo.assert_not_called();snapshot.assert_not_called()
 def test_explanation_no_analysis(self):
  with patch.object(bot,'telegram'),patch.object(bot,'route',return_value={'action':'chat','reply':'Sweep mein price level cross karke wapas close karti hai.'}),patch.object(bot,'send') as send,patch.object(bot,'analyze_for_chat') as analyze,patch.object(bot,'photo') as photo:
   bot.handle({'message':{'from':{'id':88},'text':'Sweep kya hai?','chat':{'id':88,'type':'private'}}})
   analyze.assert_not_called();photo.assert_not_called();self.assertIn('Sweep',send.call_args.args[1])


import unittest
from unittest.mock import patch
import bot,mentor
from engine import DataError
class ConversationTests(unittest.TestCase):
 def setUp(self):bot.sessions.clear();bot.cooldowns.clear()
 def message(self,text):return {'message':{'text':text,'chat':{'id':77,'type':'private'}}}
 def test_greeting(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',return_value={'action':'chat','reply':'Hi! Kaise ho?','symbol':None,'timeframe':None,'method':None}):
   bot.handle(self.message('Hello'));self.assertEqual(send.call_args.args[1],'Hi! Kaise ho?')
 def test_context(self):
  bot.session(77)['selection']={'symbol':'XAUUSD','timeframe':'15m','method':'SMC'}
  with patch.object(bot,'telegram'),patch.object(bot,'route',return_value={'action':'analyze','reply':'','symbol':None,'timeframe':None,'method':None}),patch.object(bot,'analyze_for_chat') as analyze:
   bot.handle(self.message('abhi kya scene hai?'));analyze.assert_called_once_with(77,'XAUUSD','15m','SMC')
 def test_missing_tf(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',return_value={'action':'analyze','reply':'','symbol':'XAUUSD','timeframe':None,'method':None}),patch.object(bot,'analyze_for_chat') as analyze:
   bot.handle(self.message('Gold?'));analyze.assert_not_called();self.assertIn('timeframe',send.call_args.args[1])
 def test_ai_unavailable(self):
  with patch.object(bot,'telegram'),patch.object(bot,'send') as send,patch.object(bot,'route',side_effect=DataError('quota unavailable')):
   bot.handle(self.message('hi'));self.assertEqual(send.call_args.args[1],'quota unavailable')
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

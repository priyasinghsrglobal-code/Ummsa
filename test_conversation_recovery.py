import unittest, time, tempfile, os
from unittest.mock import patch, MagicMock
from urllib.request import Request
from urllib.error import HTTPError
import bot, mentor, features
from engine import DataError

class ConversationRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patch.dict(os.environ,{'BOT_DB_PATH':self.temp.name+'/test.db'}).start()
        self.state={'history':[{'role':'assistant','text':'Old gold levels 4061.73 and 4136.50'}],'selection':{'symbol':'XAUUSD','timeframe':'15m'},'preferences':{},'watchlist':[]}
        patch('bot.session',return_value=self.state).start()
        self.send=patch('bot.send').start();patch('bot.telegram').start()
        self.route=patch('bot.route',side_effect=DataError('outage')).start()
        self.addCleanup(patch.stopall)
    def msg(self,text,quote=None):
        m={'chat':{'id':1,'type':'private'},'text':text}
        if quote:m['reply_to_message']={'text':quote}
        bot.handle_inner({'message':m})
        return self.send.call_args.args[1] if self.send.call_args else ""
    def test_screenshot_sequence(self):
        self.assertIn('ready',self.msg('hy bhai kaise ho'))
        self.assertIn('Hey',self.msg('hy'))
        self.msg('Explain a complex trading concept in detail')
        reply=self.msg('kyun','Abhi jawab dene mein dikkat aa rahi hai. Thodi der baad dobara try karein.')
        self.assertIn('AI service',reply);self.assertNotIn('4061',reply)
        self.msg('start fresh topic , forget past conversation')
        self.assertEqual(self.state['selection'],{})
        self.assertNotIn('4061',str(self.state['history']))
        self.assertIsNone(self.state['last_failure'])
    def test_plain_greeting_and_definition_need_no_ai(self):
        self.msg('hy');self.msg('SMC kya hai?')
        self.route.assert_not_called()
    def test_reset_negation(self):
        self.assertFalse(bot.reset_requested('do not start fresh'))
    def test_chart_slots_retain_image_intent(self):
        self.state['selection']={}
        with patch('bot.chart_for_chat') as chart,patch('bot.analyze_for_chat') as analysis:
            self.msg('chart dikhao');self.msg('gold');self.msg('15m')
            chart.assert_called_once_with(1,'XAUUSD','15m');analysis.assert_not_called()
            self.route.assert_not_called()
    def test_silver_bare_15_uses_chart_not_market_data(self):
        for value in ('15','15m','15 min','15 minutes'):
            self.state['selection']={'symbol':'XAUUSD','timeframe':'15m'}
            self.state['pending']=None
            with patch('bot.chart_for_chat') as chart,patch('bot.analyze_for_chat') as analysis:
                self.msg('silver ka chart dikhao');self.msg(value)
                chart.assert_called_once_with(1,'XAGUSD','15m')
                analysis.assert_not_called()
        self.route.assert_not_called()
    def test_invalid_timeframe_preserves_chart(self):
        self.state['selection']={}
        with patch('bot.chart_for_chat') as chart:
            self.msg('silver chart dikhao');self.msg('7');self.msg('15')
            chart.assert_called_once_with(1,'XAGUSD','15m')
    def test_failed_chart_keeps_retry_intent(self):
        with patch('bot.snapshot',side_effect=DataError('unavailable')):
            self.msg('silver 15m chart dikhao')
        self.assertEqual(self.state['pending']['action'],'chart')
        with patch('bot.chart_for_chat') as chart:
            self.msg('15m');chart.assert_called_once_with(1,'XAGUSD','15m')
    def test_analysis_never_sends_chart(self):
        with patch('bot.chart_for_chat') as chart,patch('bot.analyze_for_chat') as analysis:
            self.msg('gold 15m analysis');analysis.assert_called_once();chart.assert_not_called()
    def test_pending_cancels_on_topic_change(self):
        self.state['selection']={};self.msg('chart dikhao');self.msg('Explain a complex concept')
        self.assertIsNone(self.state['pending'])
    def test_unsupported_tf_not_replaced(self):
        with patch('bot.chart_for_chat') as chart:
            self.assertIn('supported nahi',self.msg('gold 1d chart dikhao'));chart.assert_not_called()
    def test_unsupported_symbol_not_replaced_with_previous_gold(self):
        with patch('bot.chart_for_chat') as chart:
            self.assertIn('verified data',self.msg('nasdaq chart dikhao'));chart.assert_not_called()
    def test_failure_saved_across_restart(self):
        self.msg('Explain a complex concept');features.save_user(1,self.state)
        loaded=features.load_user(1)
        self.assertIsNotNone(loaded['last_failure']);self.assertIn('trouble',str(loaded['history']))
    def test_about_sr_no_unrequested_url(self):
        self.assertNotIn('https://',self.msg('SR kya hai?'))

class ModelFallback(unittest.TestCase):
    def test_transient_error_uses_alternate_model(self):
        mentor.calls.clear()
        req=Request('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent',data=b'{}',headers={'x-goog-api-key':'test-only'})
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        with patch('mentor.features.event'),patch('mentor.time.sleep'),patch('mentor.urlopen',side_effect=[HTTPError(req.full_url,503,'unavailable',{},None),response]) as call:
            mentor._ai_request(req)
            self.assertIn('gemini-3.5-flash-lite',call.call_args.args[0].full_url)
            self.assertEqual(call.call_args.args[0].get_header('X-goog-api-key'),'test-only')

if __name__=='__main__':unittest.main()

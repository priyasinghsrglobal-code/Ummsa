import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
import bot, mentor
from engine import DataError
import test_conversation_recovery as recovery
from test_detailed_analysis import bars

class ChartIntent(unittest.TestCase):
    setUp = recovery.ConversationRecovery.setUp
    msg = recovery.ConversationRecovery.msg

def variants(self):
    for text in ('graph bhej', 'gold ka chrt dikha', 'chart chahiye', 'chart dikha do bhai', 'chart dekhna hai', 'chart mil sakta hai?', 'chart bhejna', 'show me the graph', 'can I see gold chart?', 'gold 15m chart', 'चार्ट दिखाओ', 'gold ka chart samjhao aur dikhao'):
        with self.subTest(text=text),patch('bot.chart_for_chat') as chart:
            self.msg(text)
            chart.assert_called_once_with(1,'XAUUSD','15m')
    self.route.assert_not_called()
ChartIntent.test_variants_without_ai=variants

def semantic(self):
    self.route.side_effect=None
    self.route.return_value={'action':'chart','reply':'Sure','symbol':'XAUUSD','timeframe':'15m'}
    with patch('bot.chart_for_chat') as chart,patch('bot.analyze_for_chat') as analysis:
        self.msg('let me see this visually')
        chart.assert_called_once_with(1,'XAUUSD','15m');analysis.assert_not_called()
ChartIntent.test_semantic_action_is_respected=semantic

def missing(self):
    self.state['selection']={}
    self.route.side_effect=None
    self.route.return_value={'action':'chart','reply':'Sure'}
    with patch('bot.chart_for_chat') as chart:
        self.msg('let me see this visually');self.msg('gold');self.msg('15')
        chart.assert_called_once_with(1,'XAUUSD','15m')
ChartIntent.test_semantic_missing_slots=missing

def refusal(self):
    self.route.side_effect=None
    self.route.return_value={'action':'chart','reply':'Okay'}
    for text in ('chart mat bhejo','chart nahi chahiye','no chart please','chart kya hai?', 'what is a chart?', 'how to read charts?','gold ka analysis'):
        with self.subTest(text=text),patch('bot.chart_for_chat') as chart,patch('bot.analyze_for_chat'):
            if text=='gold ka analysis':self.route.return_value['action']='analyze'
            self.msg(text);chart.assert_not_called()
ChartIntent.test_no_unsolicited_charts=refusal

def simple(self):
    with patch('bot.candles',return_value=bars()):
        self.msg('gold price');self.msg('gold trend and levels')
    self.route.assert_not_called()
    self.assertIn('candle close',self.send.call_args.args[1])
ChartIntent.test_simple_market_answers_without_ai=simple

class OutageRecovery(unittest.TestCase):
    def setUp(self):
        mentor.calls.clear();mentor._ai_pause_until=0
        patch('mentor.features.event').start();patch('mentor.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.addCleanup(lambda:setattr(mentor,'_ai_pause_until',0))
    def test_circuit_rejects_repeated_outage_then_recovers(self):
        error=HTTPError('https://invalid',503,'secret',{},None)
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"ok":true}'
        with patch('mentor.urlopen',side_effect=[error,error,response]) as call:
            with self.assertRaises(DataError):mentor._ai_request(object())
            with self.assertRaises(DataError):mentor._ai_request(object())
            self.assertEqual(call.call_count,2)
            mentor._ai_pause_until=0
            self.assertEqual(mentor._ai_request(object()),{'ok':True})
            self.assertEqual(call.call_count,3)
            self.assertLessEqual(call.call_args.kwargs['timeout'],8)
    def test_no_second_network_call_after_budget_exhausted(self):
        def fail(*a,**k):
            clock.return_value=113
            raise TimeoutError()
        with patch('mentor.time.monotonic',return_value=100) as clock,patch('mentor.urlopen',side_effect=fail) as call:
            with self.assertRaises(DataError):mentor._ai_request(object())
            self.assertEqual(call.call_count,1)

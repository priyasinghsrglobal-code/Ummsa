"""Regression tests for quiet, four-slot, no-neutral channel publishing."""
import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import channel_publisher as publisher
import channel_content as content
import telegram_signals as sig
import features

DUBAI=ZoneInfo("Asia/Dubai")

def stamp(hour,minute):
    return datetime(2026,11,3,hour,minute,tzinfo=DUBAI).timestamp()

def candles_for(hour):
    close=datetime(2026,11,3,hour,0,tzinfo=DUBAI).timestamp()
    return [dict(t=close-(90-i)*900,o=4000.,h=4010.,l=3990.,c=4001.) for i in range(90)]

BUY={'side':'BUY','entry':4001.,'stop':3991.,'targets':[4011.,4021.,4031.]}
SELL={'side':'SELL','entry':4001.,'stop':4011.,'targets':[3991.,3981.,3971.]}

class FourSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'BOT_DB_PATH':self.tmp.name+'/schedule.sqlite'})
        self.env.start()
        publisher._recent_checks.clear()
        self.gid=-100555333111
        sig.configure_group(self.gid,'XAUUSD','15m','direction',True)
        sig.add_group_market(self.gid,'XAGUSD','15m','direction')
        sig.add_group_market(self.gid,'BTCUSD','15m','direction')
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        publisher._recent_checks.clear()

    def test_exact_four_slots_only_and_no_duplicate(self):
        events=[]
        def publish(*args):events.append(args)
        with patch.object(sig,'calculate',return_value=BUY):
            self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(10),publish,stamp(9,59)),0)
            self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(10),publish,stamp(10,2)),1)
            self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(10),publish,stamp(10,4)),0)
            self.assertEqual(len(events),1)
            self.assertIn(events[0][4],('XAUUSD','XAGUSD','BTCUSD'))
            with patch.object(sig,'calculate',return_value=SELL):
                self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(19),publish,stamp(19,2)),1)
            self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(19),publish,stamp(19,20)),0)
        self.assertEqual(len(events),2)

    def test_neutral_no_post(self):
        posted=[]
        with patch.object(sig,'calculate',return_value={'side':'NEUTRAL'}):
            self.assertEqual(publisher.tick(lambda symbol,tf:candles_for(10),
                                            lambda *args:posted.append(args),stamp(10,2)),0)
        self.assertEqual(posted,[])

    def test_bad_data_cannot_generate_signal(self):
        posted=[]
        with patch.object(sig,'calculate',return_value=BUY):
            self.assertEqual(publisher.tick(lambda sym,tf:(_ for _ in ()).throw(ValueError('bad data')),
                                            lambda *args:posted.append(args),stamp(10,2)),0)
        self.assertEqual(posted,[])

    def test_ema_chart_looks_like_png(self):
        from chart import signal_render
        bars=candles_for(10)
        png=signal_render(bars,'XAUUSD','15m',BUY)
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')

class EngagementScheduleTests(unittest.TestCase):
    def test_weekly_content_rotation(self):
        self.assertEqual(content.engagement_due(stamp(15,30))[1],'poll')  # Tue
        self.assertIsNone(content.engagement_due(stamp(10,5)))
        self.assertIsNone(content.engagement_due(stamp(22,0)))
    def test_original_text_has_real_newlines(self):
        self.assertTrue(all('\\n' not in message for message in content.ORIGINAL_LINES))
        self.assertTrue(any('\n' in message for message in content.ORIGINAL_LINES))

if __name__=='__main__':
    unittest.main()

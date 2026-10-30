import unittest,os,tempfile,time
from unittest.mock import patch
os.environ['BOT_DB_PATH']=tempfile.mktemp(suffix='.sqlite3')
import telegram_signals as sig,features,bot
class SignalTests(unittest.TestCase):
    def setUp(self):
        with features.connect() as db:
            sig.setup_db(db)
            db.execute('DELETE FROM signal_state')
            db.execute('DELETE FROM signal_subscriptions')
            db.execute('DELETE FROM signal_delivery')
    def test_configure_and_disable(self):
        sig.configure(87,'XAUUSD','15m','direction',True)
        self.assertEqual(sig.config(87)['enabled'],1)
        sig.configure(87,'XAUUSD','15m','direction',False)
        self.assertEqual(sig.config(87)['enabled'],0)
    def test_bad_config(self):
        with self.assertRaises(ValueError):sig.configure(3,'EURUSD','5m','supply',True)
    def test_neutral_no_levels(self):
        result={'side':'NEUTRAL'}
        self.assertIn('No aligned',sig.format_signal(result,'XAUUSD','15m','direction',900))
    def test_first_scan_no_alert_then_once(self):
        bars=[dict(t=n*900,o=2400,h=2401,l=2399,c=2400) for n in range(90)]
        sig.configure(87,'XAUUSD','15m','direction',True)
        with features.connect() as db:db.execute('UPDATE signal_subscriptions SET started=0 WHERE uid=87')
        sent=[]
        with patch.object(features,'allowed',return_value=True),patch.object(sig,'calculate',return_value={'side':'NEUTRAL'}):
            self.assertEqual(sig.once('XAUUSD','15m','direction',bars,lambda uid,msg:sent.append(msg)),'BASELINE')
        future=bars+[dict(t=90*900,o=2400,h=2410,l=2398,c=2409)]
        with patch.object(features,'allowed',return_value=True),patch.object(sig,'calculate',return_value={'side':'BUY','entry':2409.,'stop':2390.,'targets':[2428.,2447.,2466.]}):
            self.assertEqual(sig.once('XAUUSD','15m','direction',future,lambda uid,msg:sent.append(msg)),'SIGNAL')
            self.assertEqual(sig.once('XAUUSD','15m','direction',future,lambda uid,msg:sent.append(msg)),'UNCHANGED')
        self.assertEqual(len(sent),1);self.assertIn('TP3:',sent[0])
    def test_message_format_has_newlines(self):
        msg=sig.format_signal({'side':'BUY','entry':4033.,'stop':4028.,'targets':[4038.,4043.,4048.]},'XAUUSD','15m','direction',900)
        self.assertIn('🟢 BUY setup\\n',msg)
        self.assertIn('🎯 T1: 4038.00\\n',msg)
        self.assertNotIn('\\\\n',msg)
    def test_neutral_chart_png(self):
        from chart import signal_render
        bars=[dict(t=n*900,o=2400,h=2401,l=2399,c=2400) for n in range(90)]
        png=signal_render(bars,'XAUUSD','15m',{'side':'NEUTRAL'})
        self.assertEqual(png[:4],bytes([137,80,78,71]))
    def test_group_subscription_lifecycle(self):
        sig.configure_group(-100123456,'XAUUSD','15m','direction',True)
        self.assertEqual(sig.group_config(-100123456)['enabled'],1)
        self.assertIn(('XAUUSD','15m','direction'),sig.tasks())
        sig.configure_group(-100123456,'XAUUSD','15m','direction',False)
        self.assertEqual(sig.group_config(-100123456)['enabled'],0)
    def test_group_admin_restriction(self):
        from unittest.mock import patch
        message={'message':{'from':{'id':77},'chat':{'id':-10012345,'type':'supergroup'},'text':'/group_on XAUUSD 15m'}}
        with patch.object(bot,'group_admin',return_value=False),patch.object(bot,'send') as send:
            bot.handle(message)
            self.assertIn('Only group admins',send.call_args.args[1])
    def test_chart_png(self):
        from chart import signal_render
        bars=[dict(t=n*900,o=2400,h=2401,l=2399,c=2400) for n in range(90)]
        png=signal_render(bars,'XAUUSD','15m',{'side':'BUY','entry':2400.,'stop':2390.,'targets':[2410.,2420.,2430.]})
        self.assertTrue(png[:4]==bytes([137,80,78,71]))
if __name__=='__main__':unittest.main()

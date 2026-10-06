import os,tempfile,unittest,time,json
from unittest.mock import patch
import features,bot,mentor

def message(uid,text):return {'message':{'from':{'id':uid},'chat':{'id':uid,'type':'private'},'text':text}}

class FeaturesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'BOT_DB_PATH':self.tmp.name+'/test.db','ADMIN_USER_IDS':'100'});self.env.start();bot.sessions.clear();bot.cooldowns.clear()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def state(self):return {'history':[],'selection':{},'preferences':{},'watchlist':[]}
 def test_private_authorizes_callback_sender(self):
  features.put_setting('private',True)
  with patch.object(bot,'send'),patch.object(bot,'telegram'),patch.object(bot,'route') as route:
   bot.handle(message(9,'Gold analysis'))
   bot.handle({'callback_query':{'id':'x','from':{'id':9},'message':{'chat':{'id':9,'type':'private'}},'data':'a:EURUSD:5m:SMC'}})
   route.assert_not_called();self.assertNotIn(9,bot.sessions)
  self.assertTrue(features.allowed(100))
 def test_admin_no_escalation(self):
  self.assertIn('Admin access',features.command(9,'/approve 9',self.state()))
  features.command(100,'/private on',self.state());self.assertFalse(features.allowed(9))
  features.command(100,'/approve 9',self.state());self.assertTrue(features.allowed(9))
  features.command(100,'/remove 9',self.state());self.assertFalse(features.allowed(9))
 def test_persistence_and_isolation(self):
  a=self.state();features.command(9,'/preferences symbol XAUUSD',a);features.save_user(9,a)
  self.assertEqual(features.load_user(9)['preferences']['symbol'],'XAUUSD');self.assertEqual(features.load_user(10)['preferences'],{})
 def test_journal_weekly_scope(self):
  a=self.state();features.command(9,'/journal EURUSD buy 100 95 110 -1 chased entry',a)
  self.assertIn('-1.00R',features.command(9,'/weekly',a));self.assertIn('No saved',features.command(10,'/weekly',a))
 def test_setup_and_invalid_risk(self):
  self.assertIn('2.00:1',features.review('buy',100,95,110))
  self.assertIn('2.00:1',features.numbers_review('review sell entry 100 sl 105 tp 90'))
  for values in [('buy',100,105,110),('sell',100,95,90),('buy',float('nan'),95,110)]:
   with self.assertRaises(ValueError):features.review(*values)
  self.assertIn('0.2 lots',features.command(9,'/risk 1000 1 50 1 0.01',self.state()))
  with self.assertRaises(ValueError):features.command(9,'/risk 1000 nan 50 1 0.01',self.state())
 def test_feedback_owner(self):
  data=features.feedback_buttons(9,'answer')[0][0]['callback_data']
  self.assertIn('no longer',features.feedback(10,data));self.assertIn('saved',features.feedback(9,data))
 def test_faq_expiration_override(self):
  self.assertIn('MetaTrader',features.faq_answer('about',self.state()))
  features.put_setting('faq:about',{'en':'outdated','verified_at':0})
  self.assertIn('re-verification',features.faq_answer('about',self.state()))
  features.command(100,'/faq_set about | Approved | Approved hi',self.state())
  self.assertEqual(features.faq_answer('about',self.state()),'Approved')
 def test_sr_works_without_ai(self):
  with patch.object(bot,'send') as send,patch.object(bot,'route',side_effect=AssertionError('AI called')):
   bot.handle(message(9,'SR kya hai?'));self.assertIn('SR Global',send.call_args.args[1])
   bot.handle(message(9,'withdrawal pending hai'));self.assertIn('support@',send.call_args.args[1])
 def test_greeting_and_reset(self):
  with patch.object(bot,'send') as send:
   bot.handle(message(9,'/start'));self.assertNotIn('seedha',send.call_args.args[1])
   bot.handle(message(9,'/preferences language english'));bot.handle(message(9,'/reset'))
  self.assertEqual(features.load_user(9)['preferences'],{})
 def test_delete(self):
  with patch.object(bot,'send'):
   bot.handle(message(9,'/journal EURUSD buy 100 95 110 2 patient'))
   bot.handle(message(9,'/delete_my_data confirm'))
  self.assertIn('No saved',features.command(9,'/weekly',self.state()))
  with features.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)
 def test_reminders_respect_revocation(self):
  features.command(9,'/remind 1 review journal',self.state())
  with features.connect() as db:db.execute('UPDATE alerts SET due=0')
  features.command(100,'/remove 9',self.state())
  with patch.object(bot,'send') as send:bot.deliver_reminders();send.assert_not_called()
 def test_explicit_chart_and_nochart(self):
  for text in ['chart mat bhejo','chart kya hai?','gold ka analysis','no chart please']:self.assertFalse(bot.wants_chart(text))
  self.assertTrue(bot.wants_chart('Gold ka chart dikhao'))
 def test_limits_are_per_user(self):
  with patch.object(bot,'send') as send:
   for i in range(13):bot.handle(message(9,'/help'))
   self.assertIn('wait',send.call_args.args[1]);bot.handle(message(10,'/help'));self.assertIn('/review',send.call_args.args[1])
 def test_persistent_history(self):
  with patch.object(bot,'send'),patch.object(bot,'telegram'),patch.object(bot,'route',return_value={'action':'chat','reply':'Explanation'}):bot.handle(message(9,'explain liquidity'))
  bot.sessions.clear();self.assertEqual(bot.session(9)['history'][-1]['text'],'Explanation')

if __name__=='__main__':unittest.main()

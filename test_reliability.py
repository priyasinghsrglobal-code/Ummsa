import json, unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
import mentor, bot
from engine import DataError

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        mentor.calls.clear()
        self.event=patch('mentor.features.event').start()
        patch('mentor.time.sleep').start()
        self.addCleanup(patch.stopall)

    def error(self,code):
        return HTTPError('https://provider.invalid',code,'private provider details',{},None)

    def test_503_recovers(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"ok":true}'
        with patch('mentor.urlopen',side_effect=[self.error(503),response]) as call:
            self.assertEqual(mentor._ai_request(object()),{'ok':True})
            self.assertEqual(call.call_count,2)
            self.assertEqual(len(mentor.calls),2)

    def test_persistent_failure_is_bounded_and_safe(self):
        with patch('mentor.urlopen',side_effect=self.error(503)) as call:
            with self.assertRaises(DataError) as exc:mentor._ai_request(object())
            self.assertEqual(call.call_count,2)
            self.assertNotIn('503',str(exc.exception))
            self.assertNotIn('/menu',str(exc.exception))

    def test_quota_and_auth_are_not_retried(self):
        for code in (429,401,403):
            mentor.calls.clear()
            with patch('mentor.urlopen',side_effect=self.error(code)) as call:
                with self.assertRaises(DataError):mentor._ai_request(object())
                self.assertEqual(call.call_count,1)

    def test_retry_respects_shared_budget(self):
        mentor.calls[:]=[mentor.time.time()]*4
        with patch('mentor.urlopen',side_effect=self.error(503)) as call:
            with self.assertRaises(DataError):mentor._ai_request(object())
            self.assertEqual(call.call_count,1)

    def test_handler_does_not_append_commands(self):
        state={'history':[],'selection':{}}
        with patch('bot.session',return_value=state), patch('bot.features.command',return_value=None), patch('bot.features.numbers_review',return_value=None), patch('bot.features.faq_topic',return_value=None), patch('bot.features.issue'), patch('bot.features.language',return_value='hi'), patch('bot.telegram'), patch('bot.send') as send, patch('bot.route',side_effect=DataError('HTTP 503 /menu')):
            bot.handle_inner({'message':{'chat':{'id':1,'type':'private'},'text':'Explain an advanced concept'}})
            reply=send.call_args.args[1]
            self.assertNotIn('503',reply)
            self.assertNotIn('/menu',reply)
            self.assertNotIn('/sr',reply)

if __name__=='__main__':unittest.main()

import os,tempfile,unittest,json,time
from unittest.mock import patch
os.environ['UMMSA_DB']=tempfile.mktemp(suffix='.sqlite3')
os.environ['UMMSA_WORKER']='false'
os.environ['COOKIE_SECURE']='false'
os.environ['OWNER_SETUP_TOKEN']='test-owner-activation-code'
import webapp
from web_engine import step_setup, candidate, VERSION

def pending():
    return dict(id='test',side='BUY',symbol='XAUUSD',timeframe='15m',status='Awaiting confirmation',
        level=100,stop=95,tolerance=.5,last_bar=1000,expires_at=10000,reason='Waiting',targets=[],strategy=VERSION)

class LifecycleTests(unittest.TestCase):
    def test_no_same_candle_confirmation(self):
        s,e=step_setup(pending(),dict(t=1000,o=100,h=102,l=99,c=101),900)
        self.assertEqual(s['status'],'Awaiting confirmation');self.assertIsNone(e)
    def test_confirmation_and_future_target(self):
        s,e=step_setup(pending(),dict(t=1900,o=100,h=102,l=99.9,c=101),900)
        self.assertEqual(s['status'],'Confirmed');self.assertEqual(s['targets'],[107,113,119])
        self.assertEqual(s['level'],100);self.assertEqual(s['entry'],101)
        s,e=step_setup(s,dict(t=2800,o=101,h=108,l=100,c=107),900)
        self.assertEqual(s['status'],'TP1 reached')
        s2,e=step_setup(s,dict(t=2800,o=101,h=108,l=100,c=107),900)
        self.assertIsNone(e);self.assertEqual(s,s2)
    def test_ambiguous_not_win(self):
        s,_=step_setup(pending(),dict(t=1900,o=100,h=102,l=99.9,c=101),900)
        s,e=step_setup(s,dict(t=2800,o=101,h=120,l=94,c=100),900)
        self.assertEqual(s['status'],'Ambiguous')
    def test_invalidation_and_expiry(self):
        s,_=step_setup(pending(),dict(t=1900,o=100,h=101,l=98,c=99),900)
        self.assertEqual(s['status'],'Invalidated')
        s,_=step_setup(pending(),dict(t=11000,o=100,h=102,l=99,c=101),900)
        self.assertEqual(s['status'],'Expired')
    def test_short_geometry(self):
        s=pending();s.update(side='SELL',stop=105)
        s,_=step_setup(s,dict(t=1900,o=100,h=100.2,l=98,c=99),900)
        self.assertEqual(s['targets'],[93,87,81]);self.assertEqual(s['status'],'Confirmed')
    def test_unsafe_volatility_blocked(self):
        bars=[dict(t=n*900,o=100,h=120,l=80,c=100) for n in range(100)]
        s,reason=candidate(bars,'XAUUSD','15m',900)
        self.assertIsNone(s);self.assertIn('Volatility',reason)

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner=webapp.app.test_client()
        r=cls.owner.post('/api/setup',json={'email':'owner@example.com','password':'strong-password-123','token':'test-owner-activation-code'})
        assert r.status_code==200,r.json
    def setUp(self):self.client=webapp.app.test_client()
    def test_anonymous_authentication(self):
        self.assertEqual(self.client.get('/api/analysis').status_code,401)
        self.assertEqual(self.client.get('/api/admin/status').status_code,401)
    def test_demo_never_persists(self):
        with webapp.db() as c:n=c.execute('SELECT count(*) FROM setups').fetchone()[0]
        for scenario in ('buy','sell','wait'):
            r=self.client.get('/api/demo?scenario='+scenario)
            self.assertEqual(r.status_code,200);self.assertEqual(r.json['mode'],'demo')
        with webapp.db() as c:self.assertEqual(n,c.execute('SELECT count(*) FROM setups').fetchone()[0])
    def test_owner_bootstrap_only_once(self):
        r=self.client.post('/api/setup',json={'email':'second@example.com','password':'strong-password-123','token':'test-owner-activation-code'})
        self.assertEqual(r.status_code,409)
    def test_access_control_and_account_isolation(self):
        payload={'email':'member@example.com','password':'strong-password-123'}
        self.client.post('/api/register',json=payload)
        r=self.client.post('/api/login',json=payload);uid=r.json['user']['id']
        self.assertEqual(self.client.get('/api/history').status_code,403)
        self.owner.post('/api/admin/user',json={'id':uid,'status':'active'})
        self.assertEqual(self.client.get('/api/history').status_code,200)
        self.assertEqual(self.client.get('/api/admin/status').status_code,403)
        self.client.post('/api/preferences',json={'watchlist':['EURUSD'],'alerts':True})
        self.assertNotEqual(self.owner.get('/api/me').json['user']['preferences'].get('watchlist'),['EURUSD'])
        self.owner.post('/api/admin/user',json={'id':uid,'status':'blocked'})
        self.assertEqual(self.client.get('/api/history').status_code,403)
    def test_csrf_and_untrusted_push(self):
        r=self.owner.post('/api/preferences',json={},headers={'Origin':'https://evil.example'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.owner.post('/api/push/subscribe',json={'endpoint':'http://127.0.0.1/secrets'}).status_code,400)
    def test_no_fabricated_provider_fallback(self):
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':''}):
            r=self.owner.get('/api/analysis?symbol=XAUUSD&tf=15m')
            self.assertEqual(r.status_code,400);self.assertNotIn('setup',r.json)
    def test_live_signal_and_notification_idempotency(self):
        self.owner.post('/api/preferences',json={'watchlist':['XAUUSD'],'alerts':True})
        bars=self.client.get('/api/demo').json['candles']
        for b in bars:b['c']=b['o']=2400;b['h']=2401;b['l']=2399
        bars[-1].update(o=2400,c=2410,h=2411,l=2400)
        with patch('webapp.provider',return_value=bars):
            r=self.owner.get('/api/analysis');self.assertEqual(r.status_code,200)
            self.assertEqual(r.json['result'],'WAIT')
            self.owner.get('/api/analysis')
        with webapp.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM transitions').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM notifications').fetchone()[0],1)
        self.assertEqual(self.owner.get('/api/history').json['signals'][0]['status'],'Awaiting confirmation')
    def test_scan_settings_validation(self):
        self.assertEqual(self.owner.post('/api/admin/scanner',json={'enabled':True,'symbols':['FAKE'],'tf':'15m'}).status_code,400)
    def test_security_headers(self):
        r=self.client.get('/')
        self.assertEqual(r.status_code,200)
        self.assertIn('frame-ancestors',r.headers['Content-Security-Policy'])
        self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')

if __name__=='__main__':unittest.main()

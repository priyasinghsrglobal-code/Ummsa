"""Ummsa private-beta web/API service. Separate from the Telegram polling worker."""
import base64, hashlib, hmac, json, logging, math, os, secrets, sqlite3, threading, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError
from flask import Flask, request, jsonify, g, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from engine import SYMBOLS, TIMEFRAMES, validate, DataError
from web_engine import VERSION, RULES, TERMINAL, process, context

ROOT=Path(__file__).parent
DB=os.getenv('UMMSA_DB','/tmp/ummsa.sqlite3')
app=Flask(__name__,static_folder='web',static_url_path='/assets')
app.config['MAX_CONTENT_LENGTH']=16000
log=logging.getLogger('ummsa')
scan_lock=threading.Lock();rate_lock=threading.Lock();limits={};cache={}
provider_calls=[];ai_calls=[];worker_started=False
TV={s:('COINBASE:'+s if s in ('BTCUSD','ETHUSD') else 'OANDA:'+s) for s in SYMBOLS}

def db():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL');return c

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,password TEXT,role TEXT,status TEXT,created REAL,preferences TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,uid TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS setups(id TEXT PRIMARY KEY,symbol TEXT,tf TEXT,data TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS transitions(id TEXT PRIMARY KEY,setup_id TEXT,status TEXT,stamp REAL,data TEXT);
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,uid TEXT,event_id TEXT,data TEXT,seen INTEGER DEFAULT 0,created REAL,UNIQUE(uid,event_id));
        CREATE TABLE IF NOT EXISTS subscriptions(endpoint TEXT PRIMARY KEY,uid TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS delivery(id TEXT PRIMARY KEY,uid TEXT,endpoint TEXT,data TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_try REAL,error TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS health(id INTEGER PRIMARY KEY AUTOINCREMENT,stamp REAL,symbol TEXT,status TEXT,message TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,stamp REAL,uid TEXT,action TEXT);
        ''')
    try:os.chmod(DB,0o600)
    except OSError:pass

def setting(key,default=None):
    with db() as c:r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(r[0]) if r else default

def put(key,value):
    with db() as c:c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))

def audit(uid,action):
    with db() as c:c.execute('INSERT INTO audit(stamp,uid,action) VALUES (?,?,?)',(time.time(),uid,action))

def limited(key,count,window=60):
    with rate_lock:
        now=time.monotonic()
        for k in list(limits):
            if not limits[k] or limits[k][-1]<now-3600:limits.pop(k,None)
        values=[x for x in limits.get(key,[]) if x>now-window]
        if len(values)>=count:return True
        values.append(now);limits[key]=values;return False

@app.before_request
def guard():
    if not request.path.startswith('/api/'):return
    # Same-origin writes plus JSON-only bodies prevent cross-site form requests.
    if request.method!='GET':
        origin=request.headers.get('Origin')
        if origin and urlparse(origin).netloc!=request.host:return jsonify(error='Origin rejected.'),403
        if not request.is_json:return jsonify(error='JSON body required.'),415
    if limited('ip:'+request.remote_addr,120):return jsonify(error='Too many requests; try again shortly.'),429
    token=request.cookies.get('ummsa_session','')
    with db() as c:
        g.user=c.execute('SELECT u.* FROM users u JOIN sessions s ON s.uid=u.id WHERE s.token=? AND s.expires>?',
                        (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone() if token else None
    public={'/api/status','/api/demo','/api/setup','/api/login','/api/register','/api/me'}
    if request.path in public:return
    if not g.user:return jsonify(error='Please sign in.'),401
    if request.path=='/api/logout':return
    if g.user['status']!='active':return jsonify(error='Private beta: your account is awaiting owner approval.'),403
    if request.path.startswith('/api/admin/') and g.user['role']!='admin':return jsonify(error='Admin access required.'),403

@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://s3.tradingview.com; style-src 'self' 'unsafe-inline'; frame-src https://*.tradingview.com; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    return response

@app.errorhandler(Exception)
def errors(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e,HTTPException):return jsonify(error=e.description),e.code
    if isinstance(e,(ValueError,KeyError,TypeError,DataError)):return jsonify(error=str(e) if isinstance(e,DataError) else 'Invalid input.'),400
    log.error('Request failed: %s',type(e).__name__)
    return jsonify(error='Service unavailable. Please retry.'),503

@app.get('/')
def index():return send_from_directory(ROOT/'web','index.html')

@app.get('/sw.js')
def sw():return send_from_directory(ROOT/'web','sw.js')

@app.get('/health')
def health():
    with db() as c:c.execute('SELECT 1').fetchone()
    return jsonify(status='ok',service='ummsa-web')

@app.get('/api/status')
def status():
    with db() as c:owner=c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    return jsonify(setup_required=not bool(owner),demo_label='SIMULATED DATA — NOT LIVE MARKET INFORMATION',
        data_configured=bool(os.getenv('TWELVE_DATA_API_KEY')),ai_configured=bool(os.getenv('GEMINI_API_KEY')),
        symbols=list(SYMBOLS),timeframes=list(TIMEFRAMES),strategy=VERSION,rules=RULES,payments='Not enabled',
        calendar='Not connected',indices='Provider mapping not configured',push=bool(setting('vapid_public')))

def credentials():
    body=request.get_json();email=str(body.get('email','')).strip().lower();password=body.get('password','')
    if not 5<=len(email)<=254 or '@' not in email or not isinstance(password,str) or not 12<=len(password)<=128:
        raise DataError('Enter a valid email and a password of 12–128 characters.')
    return body,email,password

def login_response(user):
    token=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        c.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],time.time()+86400))
    r=jsonify(user={k:user[k] for k in ('id','email','role','status')})
    r.set_cookie('ummsa_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true')=='true',samesite='Strict',max_age=86400)
    return r

@app.post('/api/setup')
def setup():
    if limited('setup',8,3600):return jsonify(error='Setup rate limit; try later.'),429
    body,email,password=credentials();expected=os.getenv('OWNER_SETUP_TOKEN','')
    if not expected or not hmac.compare_digest(str(body.get('token','')),expected):return jsonify(error='Invalid owner activation code.'),403
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():return jsonify(error='Owner already configured.'),409
        uid=secrets.token_hex(16)
        c.execute('INSERT INTO users(id,email,password,role,status,created) VALUES (?,?,?,?,?,?)',
                  (uid,email,generate_password_hash(password),'admin','active',time.time()))
        user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    audit(uid,'owner_setup');return login_response(user)

@app.post('/api/register')
def register():
    if limited('register',20,3600):return jsonify(error='Registration busy; try later.'),429
    _,email,password=credentials()
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():return jsonify(error='Owner setup is pending.'),403
        try:c.execute('INSERT INTO users(id,email,password,role,status,created) VALUES (?,?,?,?,?,?)',
                      (secrets.token_hex(16),email,generate_password_hash(password),'user','pending',time.time()))
        except sqlite3.IntegrityError:pass
    return jsonify(message='If this is a new account, access is pending owner approval. Existing users can sign in.')

@app.post('/api/login')
def login():
    _,email,password=credentials()
    if limited('login:'+email,6,900):return jsonify(error='Too many login attempts. Wait 15 minutes.'),429
    with db() as c:u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not u or not check_password_hash(u['password'],password):return jsonify(error='Email or password incorrect.'),401
    return login_response(u)

@app.get('/api/me')
def me():return jsonify(user=({k:g.user[k] for k in ('id','email','role','status')}|{'preferences':json.loads(g.user['preferences'])}) if g.user else None)

@app.post('/api/logout')
def logout():
    with db() as c:c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(request.cookies.get('ummsa_session','').encode()).hexdigest(),))
    r=jsonify(ok=True);r.delete_cookie('ummsa_session');return r

@app.post('/api/preferences')
def preferences():
    b=request.get_json();prefs={}
    prefs['watchlist']=[s for s in b.get('watchlist',[]) if s in SYMBOLS][:8]
    prefs['alerts']=bool(b.get('alerts',False))
    prefs['theme']=b.get('theme','dark') if b.get('theme') in ('dark','light') else 'dark'
    with db() as c:c.execute('UPDATE users SET preferences=? WHERE id=?',(json.dumps(prefs),g.user['id']))
    return jsonify(preferences=prefs)

def provider(symbol,tf):
    now=time.time();key=(symbol,tf)
    if key in cache and now-cache[key][0]<60:return validate(cache[key][1],symbol,tf,now)
    api_key=os.getenv('TWELVE_DATA_API_KEY','')
    if not api_key:raise DataError('Market data not configured. Live signals are blocked.')
    provider_calls[:]=[t for t in provider_calls if t>now-60]
    if len(provider_calls)>=int(os.getenv('WEB_DATA_REQUESTS_PER_MINUTE','2')):raise DataError('Market-data rate limit. Wait one minute.')
    provider_calls.append(now)
    query=urlencode(dict(symbol=SYMBOLS[symbol],interval=TIMEFRAMES[tf][0],outputsize=300,timezone='UTC',apikey=api_key))
    try:
        with urlopen('https://api.twelvedata.com/time_series?'+query,timeout=15) as r:payload=json.loads(r.read(1_000_000))
    except Exception:raise DataError('Market-data connection failed. No new signal generated.') from None
    bars=validate(payload,symbol,tf,now);cache[key]=(now,payload);return bars

def notify_event(c,event_id,signal,event):
    payload={'title':f'{signal["symbol"]} · {event["status"]}', 'body':event['reason'], 'signal_id':signal['id'],
             'symbol':signal['symbol'],'timeframe':signal['timeframe'],'timestamp':event['timestamp']}
    for u in c.execute("SELECT id,preferences FROM users WHERE status='active'").fetchall():
        prefs=json.loads(u['preferences'])
        if not prefs.get('alerts') or signal['symbol'] not in prefs.get('watchlist',[]):continue
        nid=hashlib.sha256((u['id']+event_id).encode()).hexdigest()[:32]
        created=c.execute('INSERT OR IGNORE INTO notifications(id,uid,event_id,data,created) VALUES (?,?,?,?,?)',
                          (nid,u['id'],event_id,json.dumps(payload),time.time())).rowcount
        if created:
            for sub in c.execute('SELECT endpoint FROM subscriptions WHERE uid=?',(u['id'],)).fetchall():
                did=hashlib.sha256((nid+sub['endpoint']).encode()).hexdigest()
                c.execute('INSERT OR IGNORE INTO delivery(id,uid,endpoint,data,status,next_try) VALUES (?,?,?,?,?,?)',
                          (did,u['id'],sub['endpoint'],json.dumps(payload),'pending',time.time()))

def scan(symbol,tf):
    if symbol not in SYMBOLS or tf not in TIMEFRAMES:raise DataError('Unsupported instrument or timeframe.')
    with scan_lock:
        try:bars=provider(symbol,tf)
        except DataError as e:
            with db() as c:c.execute('INSERT INTO health(stamp,symbol,status,message) VALUES (?,?,?,?)',(time.time(),symbol,'blocked',str(e)))
            raise
        step=TIMEFRAMES[tf][1]
        with db() as c:
            row=c.execute('SELECT data FROM setups WHERE symbol=? AND tf=? ORDER BY updated DESC LIMIT 1',(symbol,tf)).fetchone()
            previous=json.loads(row[0]) if row else None
            # Preserve all transitions, even if a new setup is detected after an old one terminates.
            if previous:
                from web_engine import step_setup
                old_events=[]
                for bar in bars:
                    previous,event=step_setup(previous,bar,step)
                    if event:old_events.append(event)
                save_signal(c,previous,old_events)
            s,events,reason=process(bars,symbol,tf,step,previous)
            if s:save_signal(c,s,events)
            c.execute('INSERT INTO health(stamp,symbol,status,message) VALUES (?,?,?,?)',(time.time(),symbol,'ok','Closed candles validated'))
            c.execute('DELETE FROM health WHERE stamp<?',(time.time()-7*86400,))
        ctx=context(bars)
        active=s and s['status'] in ('Confirmed','TP1 reached','TP2 reached')
        stamp=bars[-1]['t']+step
        return dict(mode='live',source='Twelve Data',timestamp=stamp,retrieved=time.time(),symbol=symbol,timeframe=tf,
                    data_quality='Validated closed candles',strategy=VERSION,context=ctx,candles=bars[-100:],setup=s,
                    result=s['side'] if active else 'WAIT',reason=s['reason'] if s and s['status'] not in TERMINAL else reason or 'No active setup.',
                    chart_symbol=TV[symbol],notice='Analysis signal — not an executed trade. Spread, slippage and news are not included.')

def save_signal(c,s,events):
    c.execute('INSERT OR REPLACE INTO setups VALUES (?,?,?,?,?)',(s['id'],s['symbol'],s['timeframe'],json.dumps(s),s['last_bar']))
    for event in events:
        eid=f'{s["id"]}:{event["status"]}:{event["timestamp"]}'
        fresh=c.execute('INSERT OR IGNORE INTO transitions VALUES (?,?,?,?,?)',(eid,s['id'],event['status'],event['timestamp'],json.dumps(event))).rowcount
        if fresh:notify_event(c,eid,s,event)

@app.get('/api/analysis')
def analysis():return jsonify(scan(request.args.get('symbol','XAUUSD'),request.args.get('tf','15m')))

@app.get('/api/demo')
def demo():
    symbol=request.args.get('symbol','XAUUSD');tf=request.args.get('tf','15m')
    if symbol not in SYMBOLS or tf not in TIMEFRAMES:raise DataError('Unsupported market.')
    bases={'XAUUSD':2400,'XAGUSD':28,'EURUSD':1.08,'GBPUSD':1.27,'USDJPY':150,'GBPJPY':190,'BTCUSD':65000,'ETHUSD':3200}
    base=bases[symbol];step=TIMEFRAMES[tf][1];end=1791979200;bars=[];last=base
    for n in range(100):
        close=base*(1+.00013*n+.002*math.sin(n/5));rng=base*.0008
        bars.append(dict(t=end-(100-n)*step,o=last,h=max(last,close)+rng,l=min(last,close)-rng,c=close));last=close
    side=request.args.get('scenario','buy').upper()
    if side not in ('BUY','SELL','WAIT'):side='WAIT'
    sign=1 if side=='BUY' else -1;risk=base*.004
    s=None if side=='WAIT' else dict(id='demo-'+side.lower(),symbol=symbol,timeframe=tf,side=side,status='Confirmed',
        level=last,entry=last,stop=last-sign*risk,targets=[last+sign*risk*n for n in (1,2,3)],rr=2,
        strategy='Illustrative demo fixture',created_at=end,confirmed_at=end,expires_at=end+8*step,
        reason='Illustrative confirmed setup for preview only; not calculated from live markets.')
    return jsonify(mode='demo',source='Simulated fixture',timestamp=end,symbol=symbol,timeframe=tf,context=context(bars),candles=bars,
                   setup=s,result=side,reason=s['reason'] if s else 'Illustrative WAIT: retest confirmation is missing.',
                   strategy='Demo fixture',data_quality='SIMULATED',notice='SIMULATED DATA — NOT LIVE MARKET INFORMATION')

@app.get('/api/history')
def history():
    with db() as c:
        rows=c.execute('SELECT data FROM setups ORDER BY updated DESC LIMIT 200').fetchall()
        events=c.execute('SELECT setup_id,status,stamp,data FROM transitions ORDER BY stamp DESC LIMIT 300').fetchall()
    return jsonify(signals=[json.loads(r[0]) for r in rows],events=[dict(r) for r in events])

@app.get('/api/notifications')
def notifications():
    with db() as c:rows=c.execute('SELECT id,data,seen,created FROM notifications WHERE uid=? ORDER BY created DESC LIMIT 100',(g.user['id'],)).fetchall()
    return jsonify(items=[dict(r)|{'data':json.loads(r['data'])} for r in rows])

@app.post('/api/notifications/read')
def read_notifications():
    with db() as c:c.execute('UPDATE notifications SET seen=1 WHERE uid=?',(g.user['id'],))
    return jsonify(ok=True)

@app.post('/api/explain')
def explain():
    if limited('explain:'+g.user['id'],3,60):return jsonify(error='AI limit: try again in one minute.'),429
    b=request.get_json();facts=scan(b.get('symbol','XAUUSD'),b.get('tf','15m'))
    facts.pop('candles',None);key=os.getenv('GEMINI_API_KEY','')
    fallback=f"{facts['result']}: {facts['reason']} Data: {facts['source']}. Strategy: {VERSION}."
    if not key:return jsonify(text=fallback,source='Rule-based explanation',timestamp=facts['timestamp'],strategy=VERSION)
    if limited('ai_global',5,60):return jsonify(text=fallback,source='Rule-based fallback: AI capacity reached',timestamp=facts['timestamp'],strategy=VERSION)
    prompt='Explain ONLY these computed facts in at most 100 words. Do not add prices, forecasts or recommendations. Do not override result or call WAIT a confirmed entry. Explain confirmation, invalidation and missing data. Clearly say spread, slippage and news are unverified. Facts: '+json.dumps(facts)
    payload={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'temperature':0,'maxOutputTokens':400}}
    model=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
    try:
        req=Request(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key})
        with urlopen(req,timeout=12) as r:answer=json.loads(r.read(30000))
        text=''.join(p.get('text','') for p in answer['candidates'][0]['content']['parts'])[:1800]
        if not text:raise ValueError()
        return jsonify(text=text,source='AI explanation of calculated facts',timestamp=facts['timestamp'],strategy=VERSION)
    except Exception:return jsonify(text=fallback,source='Rule-based fallback: AI unavailable',timestamp=facts['timestamp'],strategy=VERSION)

def vapid_init():
    if setting('vapid_public'):return
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key=ec.generate_private_key(ec.SECP256R1())
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    public=base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    put('vapid_private',private);put('vapid_public',public)

@app.get('/api/push/key')
def push_key():return jsonify(key=setting('vapid_public'))

@app.post('/api/push/subscribe')
def push_subscribe():
    b=request.get_json();endpoint=b.get('endpoint','');u=urlparse(endpoint)
    # Prevent SSRF: only established browser push service hosts, no custom ports/credentials.
    allowed=('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com','notify.windows.com')
    if u.scheme!='https' or u.port not in (None,443) or u.username or not any(u.hostname==h or (u.hostname or '').endswith('.'+h) for h in allowed):raise DataError('Unsupported browser push endpoint.')
    keys=b.get('keys',{})
    if not all(isinstance(keys.get(k),str) and 10<len(keys[k])<256 for k in ('auth','p256dh')):raise DataError('Invalid push keys.')
    with db() as c:
        owner=c.execute('SELECT uid FROM subscriptions WHERE endpoint=?',(endpoint,)).fetchone()
        if owner and owner[0]!=g.user['id']:return jsonify(error='Subscription belongs to another account.'),409
        c.execute('INSERT OR REPLACE INTO subscriptions VALUES (?,?,?)',(endpoint,g.user['id'],json.dumps(b)))
    return jsonify(ok=True)

@app.post('/api/push/unsubscribe')
def push_unsubscribe():
    with db() as c:c.execute('DELETE FROM subscriptions WHERE uid=?',(g.user['id'],))
    return jsonify(ok=True)

@app.get('/api/admin/status')
def admin_status():
    with db() as c:
        users=[dict(r) for r in c.execute('SELECT id,email,role,status,created FROM users ORDER BY created DESC LIMIT 200')]
        logs=[dict(r) for r in c.execute('SELECT * FROM health ORDER BY stamp DESC LIMIT 40')]
        delivery=[dict(r) for r in c.execute('SELECT id,status,attempts,error FROM delivery ORDER BY next_try DESC LIMIT 40')]
    return jsonify(users=users,health=logs,delivery=delivery,scanner=setting('scanner',{'enabled':False,'symbols':['XAUUSD'],'tf':'15m'}))

@app.post('/api/admin/user')
def admin_user():
    b=request.get_json();status=b.get('status')
    if status not in ('active','blocked'):raise DataError('Invalid account state.')
    with db() as c:c.execute("UPDATE users SET status=? WHERE id=? AND role!='admin'",(status,b.get('id')))
    audit(g.user['id'],'user_access:'+str(b.get('id'))+':'+status);return jsonify(ok=True)

@app.post('/api/admin/scanner')
def admin_scanner():
    b=request.get_json();symbols=b.get('symbols',[]);tf=b.get('tf')
    if not isinstance(symbols,list) or not 1<=len(symbols)<=4 or any(s not in SYMBOLS for s in symbols) or tf not in TIMEFRAMES:raise DataError('Choose 1–4 supported markets and a timeframe.')
    config={'enabled':bool(b.get('enabled')),'symbols':list(dict.fromkeys(symbols)),'tf':tf}
    put('scanner',config);audit(g.user['id'],'scanner:'+json.dumps(config));return jsonify(scanner=config)

def deliver_push():
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    with db() as c:rows=c.execute("SELECT d.*,s.data AS subscription,u.status AS user_status,u.preferences FROM delivery d LEFT JOIN subscriptions s ON d.endpoint=s.endpoint JOIN users u ON d.uid=u.id WHERE d.status='pending' AND d.next_try<=? LIMIT 10",(time.time(),)).fetchall()
    for row in rows:
        status='sent';error=None
        if not row['subscription'] or row['user_status']!='active' or not json.loads(row['preferences']).get('alerts'):
            status='cancelled'
        else:
            try:webpush(json.loads(row['subscription']),row['data'],vapid_private_key=Vapid.from_pem(setting('vapid_private').encode()),vapid_claims={'sub':os.getenv('VAPID_SUBJECT','mailto:admin@example.com')},timeout=8)
            except WebPushException as e:
                code=e.response.status_code if e.response is not None else 0
                error=f'Push service HTTP {code}'
                status='failed' if code in (404,410) or row['attempts']>=4 else 'pending'
                if code in (404,410):
                    with db() as c:c.execute('DELETE FROM subscriptions WHERE endpoint=?',(row['endpoint'],))
            except Exception:status='failed' if row['attempts']>=4 else 'pending';error='Push service unavailable'
        with db() as c:c.execute('UPDATE delivery SET status=?,attempts=attempts+1,next_try=?,error=? WHERE id=?',(status,time.time()+min(3600,60*2**row['attempts']),error,row['id']))

def worker():
    cursor=0;next_scan=0
    while True:
        try:
            cfg=setting('scanner',{})
            if cfg.get('enabled') and time.time()>=next_scan:
                symbols=cfg.get('symbols',['XAUUSD']);sym=symbols[cursor%len(symbols)];cursor+=1
                try:scan(sym,cfg.get('tf','15m'))
                except DataError:pass
                next_scan=time.time()+max(60,int(os.getenv('SCAN_INTERVAL_SECONDS','90')))
            deliver_push()
        except Exception:log.warning('Background task failed; next cycle will retry.')
        time.sleep(10)

init();vapid_init()
if os.getenv('UMMSA_WORKER','true')=='true':threading.Thread(target=worker,daemon=True).start()

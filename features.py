"""Persistent, deterministic bot features; no trading credentials or orders."""
import os, json, sqlite3, time, re, math
from pathlib import Path
from collections import Counter
from engine import SYMBOLS, TIMEFRAMES

WELCOME = 'Hi 👋 Welcome to SR Global Markets! Main SR ka AI assistant hoon. Trading samajhni ho, market analysis chahiye ya SR ke baare mein kuch poochna ho—bataiye, kaise help karun?'
HELP = '''Aap normal language mein sawaal pooch sakte hain. Chart sirf maangne par bhejunga.
/menu — market analysis
/sr — SR information /support — client help /ib — partnership help
/preferences — language, symbol, timeframe, style
/watchlist — saved symbols
/review buy ENTRY SL TP — setup maths
/risk BALANCE RISK_PERCENT SL_TICKS TICK_VALUE LOT_STEP — lot sizing
/journal — save a trade /weekly — last 7 days review
/learn — short lessons /alerts — reminders guide
/myid — your Telegram ID /reset — clear conversation and preferences
/delete_my_data confirm — erase your saved data'''
# Conservative source summaries, verified 2026-10-06. Never ingest website price widgets.
FAQ = {
 'about': {'en':'SR Global Markets provides forex/CFD trading and MetaTrader 5. Official website: https://srglobalmarkets.com/', 'hi':'SR Global Markets forex/CFD trading aur MetaTrader 5 provide karta hai. Official website: https://srglobalmarkets.com/', 'source':'https://srglobalmarkets.com/'},
 'accounts': {'en':'The website lists Zero, Standard and ECN accounts, plus Copy Trading and PAMM. Some published spread, lot-limit and deposit figures conflict; confirm applicable terms with support before funding.', 'hi':'Website par Zero, Standard aur ECN accounts, saath mein Copy Trading aur PAMM listed hain. Kuch spread, lot-limit aur deposit figures mein difference hai; funding se pehle applicable terms support se confirm karein.', 'source':'https://srglobalmarkets.com/accounts/'},
 'register': {'en':'Register at https://app.srglobalmarkets.com/ using the Open Account option on the official website, then complete verification in the secure client portal.', 'hi':'Official website ke Open Account option se https://app.srglobalmarkets.com/ par register karein, phir secure client portal mein verification complete karein.', 'source':'https://srglobalmarkets.com/'},
 'kyc': {'en':'The account FAQ requests a valid ID and proof of address. Check the client portal for the exact accepted documents; upload them there, not in this bot.', 'hi':'Account FAQ mein valid ID aur address proof manga gaya hai. Exact accepted documents client portal mein check karke wahin upload karein, bot mein nahi.', 'source':'https://srglobalmarkets.com/accounts/'},
 'mt5': {'en':'SR lists MT5 for desktop and mobile, and a web platform. Use the exact server and login shown in your client portal. Never share your password here.', 'hi':'SR par MT5 desktop/mobile aur web platform listed hain. Client portal mein diya exact server aur login use karein. Password yahan share na karein.', 'source':'https://srglobalmarkets.com/'},
 'support': {'en':'Contact support@srglobalmarkets.com or use https://srglobalmarkets.com/contact/ for account/payment help. I cannot access your CRM, check payment status or create a support ticket.', 'hi':'Account/payment help ke liye support@srglobalmarkets.com ya https://srglobalmarkets.com/contact/ use karein. Main CRM access, payment status check ya support ticket create nahi kar sakta.', 'source':'https://srglobalmarkets.com/'},
 'ib': {'en':'For an SR partnership/IB enquiry, contact the official team via https://srglobalmarkets.com/contact/ and ask for the partnerships desk. Commission terms need confirmation from that team.', 'hi':'SR partnership/IB enquiry ke liye https://srglobalmarkets.com/contact/ par official team se partnerships desk maangein. Commission terms team se confirm karne honge.', 'source':'https://srglobalmarkets.com/contact/'},
 'bonus': {'en':'The website advertises a 100% deposit bonus for Zero. Eligibility, withdrawal effects and current conditions must be confirmed in the approved offer terms; I will not assume them.', 'hi':'Website par Zero ke liye 100% deposit bonus advertised hai. Eligibility, withdrawal effect aur current conditions approved offer terms se confirm karein; main rules assume nahi karunga.', 'source':'https://srglobalmarkets.com/accounts/'},
 'regulation': {'en':'The website lists Mauritius FSC investment-dealer licence GB25205088 and UAE CMA licence 20200000413 under SRFX Financial Consultation. These refer to different entities/scopes; confirm the entity serving your account and its permissions.', 'hi':'Website par Mauritius FSC investment-dealer licence GB25205088 aur SRFX Financial Consultation ke under UAE CMA licence 20200000413 listed hain. Dono ka entity/scope alag hai; apne account ki entity aur permissions confirm karein.', 'source':'https://srglobalmarkets.com/'}
}

# Public company facts checked 2026-10-09; no website marketing claims of guaranteed returns.
def _fact(en, hi, page):
    return {'en':en, 'hi':hi, 'source':'https://srglobalmarkets.com/'+page, 'verified_at':1791504000}
FAQ.update({
 'accounts':_fact('Three main trading accounts: Zero, Standard and ECN. Copy Trading and PAMM are also listed separately.', '3 main trading accounts hain: Zero, Standard aur ECN. Copy Trading aur PAMM bhi alag listed hain.', 'accounts/'),
 'ecn':_fact('Yes, SR lists an ECN account with raw spreads.', 'Haan, SR mein raw spreads wala ECN account hai.', 'accounts/'),
 'spreads':_fact('Published starting spreads: Zero and ECN 0.0 pips. Standard shows conflicting 0.5 and 5.0 figures; confirm that rate with support. These are not live quotes.', 'Zero aur ECN ka advertised spread 0.0 pips se hai. Standard par 0.5 aur 5.0 dono figures hain; exact rate confirm karna hoga. Yeh live quotes nahi hain.', 'accounts/'),
 'office':_fact('Dubai regional office: #2304, Zone A, Aspect Tower, Business Bay, Dubai, UAE. Mauritius main office: fourth floor, The Docks 4, Caudan, Port Louis.', 'Dubai regional office: #2304, Zone A, Aspect Tower, Business Bay, Dubai, UAE. Mauritius main office: fourth floor, The Docks 4, Caudan, Port Louis.', 'about/'),
 'office_facilities':_fact('Office facilities, visit hours, staff names and training-room availability are not verified. Please confirm your visit with the team.', 'Office ki facilities, visit timings, staff names aur training room ki verified details abhi nahi hain. Visit se pehle team se confirm kar lein.', 'about/'),
 'phone':_fact('SR lists +971 56 452 0909 as its sales line. For support: support@srglobalmarkets.com.', 'SR ka listed sales number +971 56 452 0909 hai. Support email: support@srglobalmarkets.com.', 'contact/'),
 'support':_fact('Support: support@srglobalmarkets.com (advertised 24/5). Accounts: accounts@srglobalmarkets.com. I cannot see your account or payment status.', 'Support: support@srglobalmarkets.com (website par 24/5). Accounts team: accounts@srglobalmarkets.com. Aapka account ya payment status mere paas nahi dikhta.', 'contact/'),
 'ib':_fact('Partnership enquiries: partners@srglobalmarkets.com. The team must confirm your IB commission terms.', 'IB/partnership ke liye partners@srglobalmarkets.com par baat karein. Commission terms team confirm karegi.', 'contact/'),
 'account_terms':_fact('Website account cards advertise: Zero deposit $100, maximum $1,000, zero commission, leverage up to 1:500. Standard deposit band $100–$5,000, zero commission, forex/metals/energy leverage 1:200. ECN deposit $5,000+, commission shown as $10, leverage 1:100. Commission charging basis must be confirmed. These are advertised terms, not a personalised offer or live trading conditions.', 'Website cards: Zero deposit $100, maximum $1,000, commission zero, leverage up to 1:500. Standard deposit band $100–$5,000, commission zero, forex/metals/energy leverage 1:200. ECN deposit $5,000+, commission $10 listed, leverage 1:100. Commission kis basis par charge hota hai team se confirm karein. Yeh advertised terms hain.', ''),
 'services':_fact('SR lists forex, commodities and indices, MT5/web/mobile platforms, education and calculators. Availability depends on your account.', 'SR par forex, commodities, indices, MT5/web/mobile platforms, education aur calculators listed hain. Availability account par depend karti hai.', ''),
})

# Customer-facing voice; source metadata remains separate for factual grounding.
for _topic, _en, _hi in [
 ('about','Welcome to SR Global Markets! We offer forex/CFD trading with MT5. What would you like to know?', 'SR Global Markets mein welcome! Hum forex/CFD trading aur MT5 provide karte hain. Kya jaanna chahoge?'),
 ('accounts','We have three main trading accounts: Zero, Standard and ECN. We also offer Copy Trading and PAMM.', 'Hamare 3 main trading accounts hain: Zero, Standard aur ECN. Copy Trading aur PAMM bhi available hain.'),
 ('ecn','Yes, we offer an ECN account with raw spreads.', 'Haan, hamare yahan raw spreads wala ECN account available hai.'),
 ('spreads','Our Zero and ECN spreads start from 0.0 pips and vary with the market. Our Standard rate needs confirmation from our team; I do not have a verified figure for it.', 'Hamare Zero aur ECN accounts mein spreads 0.0 pips se shuru hote hain; market ke saath badalte hain. Standard ka exact spread hamari team se confirm karna hoga.'),
 ('office','Our Dubai office is #2304, Zone A, Aspect Tower, Business Bay. Our Mauritius main office is on the fourth floor, The Docks 4, Caudan, Port Louis.', 'Hamara Dubai office #2304, Zone A, Aspect Tower, Business Bay mein hai. Mauritius main office fourth floor, The Docks 4, Caudan, Port Louis mein hai.'),
 ('phone','You can reach our sales team on +971 56 452 0909. For support, email support@srglobalmarkets.com.', 'Hamari sales team ka number +971 56 452 0909 hai. Support ke liye support@srglobalmarkets.com par message kar sakte ho.'),
 ('support','Our support team is available at support@srglobalmarkets.com, 24/5. I cannot view your account or payment status here.', 'Hamari support team support@srglobalmarkets.com par 24/5 available hai. Yahan se aapka account ya payment status check nahi kar sakta.'),
 ('ib','For an IB partnership, contact our team at partners@srglobalmarkets.com. They will confirm the applicable commission terms.', 'IB partnership ke liye hamari team se partners@srglobalmarkets.com par baat karo. Commission terms team confirm karegi.'),
]:
    FAQ[_topic].update(en=_en,hi=_hi)


def connect():
    path=os.getenv('BOT_DB_PATH','data/bot.sqlite3');Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access(id INTEGER PRIMARY KEY, allowed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER,stamp REAL,data TEXT);
CREATE TABLE IF NOT EXISTS replies(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER,stamp REAL,kind TEXT,rating TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,stamp REAL,kind TEXT);
CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER,stamp REAL,question TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER,due REAL,text TEXT);''')
    db.execute('DELETE FROM issues WHERE stamp<?',(time.time()-7*86400,))
    db.execute('DELETE FROM replies WHERE stamp<?',(time.time()-30*86400,))
    db.execute('DELETE FROM events WHERE stamp<?',(time.time()-30*86400,))
    db.commit()
    return db

def setting(key,default=None):
    with connect() as db:r=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(r[0]) if r else default

def put_setting(key,value):
    with connect() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))

def load_user(uid):
    with connect() as db:r=db.execute('SELECT data FROM users WHERE id=?',(uid,)).fetchone()
    return json.loads(r[0]) if r else {'history':[],'selection':{},'preferences':{},'watchlist':[]}

def save_user(uid,state):
    clean={k:state.get(k,v) for k,v in [('history',[]),('selection',{}),('preferences',{}),('watchlist',[]),('pending',None),('last_failure',None)]}
    clean['history']=clean['history'][-12:]
    with connect() as db:db.execute('INSERT OR REPLACE INTO users VALUES (?,?)',(uid,json.dumps(clean)))

def owners():
    return {int(x) for x in os.getenv('ADMIN_USER_IDS','').split(',') if x.strip().isdigit()}

def allowed(uid):
    if uid in owners():return True
    with connect() as db:r=db.execute('SELECT allowed FROM access WHERE id=?',(uid,)).fetchone()
    if r:return bool(r[0])
    return not setting('private',os.getenv('PRIVATE_MODE','false').lower()=='true')

def event(kind):
    with connect() as db:
        db.execute('INSERT INTO events(stamp,kind) VALUES (?,?)',(time.time(),kind))
        db.execute('DELETE FROM events WHERE stamp<?',(time.time()-30*86400,))

def issue(uid,text):
    # Explicit text-redaction for common credentials; users can delete all their data.
    text=re.sub(r'(?i)(password|token|api.?key|otp)\s*[:=]?\s*\S+',r'\1 [redacted]',text)
    with connect() as db:
        db.execute('INSERT INTO issues(uid,stamp,question) VALUES (?,?,?)',(uid,time.time(),text[:400]))
        db.execute('DELETE FROM issues WHERE stamp<?',(time.time()-7*86400,))

def feedback_buttons(uid,kind):
    with connect() as db:
        i=db.execute('INSERT INTO replies(uid,stamp,kind) VALUES (?,?,?)',(uid,time.time(),kind)).lastrowid
        db.execute('DELETE FROM replies WHERE stamp<?',(time.time()-30*86400,))
    return [[{'text':'👍 Useful','callback_data':f'fb:{i}:yes'},{'text':'👎 Not useful','callback_data':f'fb:{i}:no'}]]

def feedback(uid,data):
    match=re.fullmatch(r'fb:(\d+):(yes|no)',data)
    if not match:return 'Invalid feedback.'
    with connect() as db:
        n=db.execute('UPDATE replies SET rating=? WHERE id=? AND uid=?',(match[2],int(match[1]),uid)).rowcount
    return 'Thank you—feedback saved.' if n else 'This reply is no longer available.'

def language(text,state):
    pref=state.get('preferences',{}).get('language')
    if pref in ('english','hinglish','hindi'):return 'en' if pref=='english' else 'hi'
    return 'hi' if re.search(r'\b(kya|hai|hain|kitne|kitna|kahan|kaha|ho|bhai|kyun|kyu|ka|kaise|mujhe|batao|chahiye|karna|nhi|nahi)\b|[\u0900-\u097f]',text.lower()) else 'en'

def faq_answer(topic,state,text=''):
    entry=setting('faq:'+topic) or FAQ.get(topic)
    if not entry:return None
    if time.time()-entry.get('verified_at',1791288000)>30*86400:
        return 'SR information needs re-verification. Please contact support@srglobalmarkets.com for the current terms.'
    return entry.get(language(text,state),entry.get('en',''))

def faq_topic(text):
    """Only short, unambiguous FAQ questions bypass conversation reasoning."""
    t=re.sub(r'[^a-z0-9\s]', ' ', text.lower());t=' '.join(t.split())
    if len(t.split())>18 or re.search(r'\b(and|aur|but|lekin|compare|difference|why|kyun|explain)\b',t):return None
    if re.fullmatch(r'(sr (global markets )?(me |mein )?)?(kitne|kitne types ke|kitne tarah ke) accounts?( hai| hain)?',t):return 'accounts'
    if re.search(r'\baccounts?\b',t) and re.search(r'\b(types?|kinds?|kitne|many)\b',t):return 'accounts'
    if re.fullmatch(r'(sr (me |mein )?)?ecn( hai| hai kya| available| available hai)?',t):return 'ecn'
    if re.search(r'\b(support|contact|sales|sr)\b',t) and re.search(r'\b(number|phone|call|mobile)\b',t):return 'phone'
    if re.search(r'\b(office|offices)\b',t) and re.search(r'\b(where|kaha|kahan|address|location|located)\b',t):return 'office'
    if re.fullmatch(r'(sr |sr global markets |sr global )?(kya hai|ke baare me batao|ke bare mein batao|about|information)',t) or t=='what is sr global markets':return 'about'
    if re.search(r'\bspreads?\b',t) and re.search(r'\b(sr|srglobal|srglobalmarkets)\b',t) and not re.search(r'\b(ecn|zero|standard|live|abhi|current|gold|xauusd|eurusd|meaning|definition)\b',t):return 'spreads'
    if re.fullmatch(r'(sr )?(support|customer care)( email| email do| email kya hai)?',t):return 'support'
    if re.fullmatch(r'(sr )?(ib|partnership)( contact| email| email do)?',t):return 'ib'
    # Complex, contextual and general trading questions go to the grounded AI.
    return None

def knowledge():
    # One database snapshot instead of three connections per knowledge topic.
    with connect() as db:
        overrides={row[0][4:]:json.loads(row[1]) for row in db.execute("SELECT key,value FROM settings WHERE key LIKE 'faq:%'")}
    result={}
    for topic,default in FAQ.items():
        entry=overrides.get(topic) or default
        stamp=entry.get('verified_at',1791288000)
        answer=entry.get('en','') if time.time()-stamp<=30*86400 else 'Needs re-verification with our support team.'
        result[topic]={'answer':answer,'source':entry.get('source'),'verified_at':stamp}
    return result

def review(side,entry,sl,tp):
    vals=[float(x) for x in (entry,sl,tp)]
    if not all(math.isfinite(x) and x>0 for x in vals):raise ValueError('Prices must be finite and positive.')
    e,s,t=vals
    if side not in ('buy','sell') or not (s<e<t if side=='buy' else t<e<s):raise ValueError('Buy: SL < entry < TP. Sell: TP < entry < SL.')
    rr=abs(t-e)/abs(e-s)
    return f'Planned reward:risk = {rr:.2f}:1. SL distance {abs(e-s):g}; target distance {abs(t-e):g}. This checks your supplied prices only. Structure, confirmation, spread/slippage and live market validity still need checking; RR alone does not validate a trade.'

def numbers_review(text):
    side=re.search(r'\b(buy|sell)\b',text.lower())
    vals=[re.search(r'\b'+label+r'\s*[:=@]?\s*(\d+(?:\.\d+)?)',text.lower()) for label in ('entry','sl','tp')]
    if side and all(vals):return review(side[1],*[v[1] for v in vals])
    return None

LESSONS={
 '1':'Price action: price ke swing highs/lows aur reactions ko observe karna. Higher highs + higher lows bullish structure suggest karte hain; har green candle buy signal nahi hoti.',
 '2':'SMC: structure, liquidity aur imbalances ka framework. Previous high ke upar wick aur neeche close possible sweep hai; institutional intent ka proof nahi.',
 '3':'Risk: entry se pehle stop aur cash loss decide karein. Stop distance aur tick value se position size calculate hoti hai; leverage safe lot size decide nahi karta.',
 '4':'Psychology: trade se pehle reason likhein. Loss ke turant baad size badhana ya stop door karna plan ko change karta hai. Journal mein triggers note karein.'}

def command(uid,text,state):
    parts=text.split();cmd=parts[0].split('@')[0].lower() if parts else '';args=parts[1:]
    if cmd=='/myid':return f'Your Telegram user ID: {uid}'
    if cmd=='/help':return HELP
    if cmd=='/preferences':
        if not args:return 'Saved: '+json.dumps(state.get('preferences',{}))+'\nSet: /preferences language english|hinglish|hindi; symbol XAUUSD; timeframe 15m; style scalping|intraday|swing (one setting per message).'
        if len(args)!=2:return 'Example: /preferences language english'
        k,v=args[0].lower(),args[1];v=v.upper() if k=='symbol' else v.lower()
        choices={'language':('english','hinglish','hindi'),'symbol':SYMBOLS,'timeframe':TIMEFRAMES,'style':('scalping','intraday','swing')}
        if k not in choices or v not in choices[k]:return 'Invalid preference. /preferences shows the supported settings.'
        state.setdefault('preferences',{})[k]=v
        if k in ('symbol','timeframe'):state['selection'][k]=v
        return f'Saved {k}: {v}.'
    if cmd=='/watchlist':
        if not args:return 'Watchlist: '+', '.join(state.get('watchlist',[]))+'\nSet: /watchlist XAUUSD EURUSD (max 8). /watchlist clear removes it.'
        values=list(dict.fromkeys(x.upper() for x in args))
        if args==['clear']:values=[]
        if len(values)>8 or any(x not in SYMBOLS for x in values):return 'Use supported symbols: '+', '.join(SYMBOLS)
        state['watchlist']=values;return 'Watchlist saved. This does not enable price notifications.'
    if cmd in ('/sr','/support','/ib'):
        topic={'/support':'support','/ib':'ib'}.get(cmd,args[0] if args else 'about')
        return faq_answer(topic,state,text) or 'Topics: '+', '.join(FAQ)
    if cmd=='/review':
        if len(args)!=4:return 'Use /review buy ENTRY SL TP, or write: buy entry 100 sl 95 tp 110. These are example prices.'
        return review(*args)
    if cmd=='/risk':
        if len(args)!=5:return 'Use /risk BALANCE RISK_PERCENT SL_TICKS TICK_VALUE_PER_LOT LOT_STEP. Tick value must be in your account currency, from your broker symbol specification.'
        balance,pct,ticks,value,step=map(float,args)
        if not all(math.isfinite(v) and v>0 for v in (balance,pct,ticks,value,step)) or pct>100:raise ValueError('Use positive finite values and risk percent <=100.')
        cash=balance*pct/100;lots=math.floor((cash/(ticks*value))/step+1e-10)*step
        return f'Risk budget: {cash:g} account-currency units. Rounded-down size: {lots:g} lots. Verify broker min/max volume and supplied tick value. Costs, gaps and slippage can increase the loss; this is not a trade recommendation.'
    if cmd=='/learn':return LESSONS.get(args[0] if args else '', 'Choose /learn 1 price action, /learn 2 SMC, /learn 3 risk, /learn 4 psychology.')
    if cmd=='/journal':
        if len(args)<7:return 'Save: /journal SYMBOL buy|sell ENTRY SL TP RESULT_R reason/tags\nExample: /journal EURUSD buy 1.10 1.09 1.12 -1 chased entry\nRESULT_R is your recorded realised result in risk units. /weekly reviews 7 days.'
        sym,side,e,s,t,r=args[:6];review(side,e,s,t);r=float(r)
        if sym.upper() not in SYMBOLS or not math.isfinite(r):raise ValueError('Use a supported symbol and finite realised R.')
        row={'symbol':sym.upper(),'side':side,'entry':float(e),'sl':float(s),'tp':float(t),'r':r,'reason':' '.join(args[6:])[:500]}
        with connect() as db:i=db.execute('INSERT INTO journal(uid,stamp,data) VALUES (?,?,?)',(uid,time.time(),json.dumps(row))).lastrowid
        return f'Trade #{i} saved from your input. /weekly shows your review.'
    if cmd=='/weekly':
        with connect() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM journal WHERE uid=? AND stamp>=? ORDER BY stamp DESC LIMIT 500',(uid,time.time()-7*86400))]
        if not rows:return 'No saved trades in the last 7 days. /journal shows how to record one.'
        tags=Counter(tag for row in rows for tag in ('chas','overtrad','sl moved','revenge','fomo') if tag in row['reason'].lower())
        return f'Last 7 days: {len(rows)} recorded trades; {sum(r["r"] for r in rows):+.2f}R total; {sum(r["r"]>0 for r in rows)} wins. Notes mention: '+(', '.join(f'{k}: {v}' for k,v in tags.items()) or 'no common mistake tags')+'. Based only on your entries; no MT5 connection.'
    if cmd=='/delete_my_data':
        if args!=['confirm']:return 'Use /delete_my_data confirm to erase your preferences, conversation, journal, reminders and feedback.'
        with connect() as db:
            db.execute('DELETE FROM users WHERE id=?',(uid,))
            for table in ('journal','replies','issues','alerts'):db.execute(f'DELETE FROM {table} WHERE uid=?',(uid,))
        state.clear();state.update(history=[],selection={},preferences={},watchlist=[])
        return 'Your saved data has been erased.'
    if cmd=='/alerts':return 'Optional journal reminder: /remind MINUTES message (1–10080 minutes, max 5). /reminders lists IDs; /cancel_reminder ID removes one. Live price/news alerts are not enabled.'
    if cmd=='/remind':
        if len(args)<2:return 'Example: /remind 60 Review today’s journal'
        minutes=int(args[0])
        if not 1<=minutes<=10080:raise ValueError('Choose 1–10080 minutes.')
        with connect() as db:
            if db.execute('SELECT count(*) FROM alerts WHERE uid=?',(uid,)).fetchone()[0]>=5:return 'Maximum 5 reminders. Cancel an existing reminder first.'
            i=db.execute('INSERT INTO alerts(uid,due,text) VALUES (?,?,?)',(uid,time.time()+60*minutes,' '.join(args[1:])[:300])).lastrowid
        return f'Reminder #{i} saved for {minutes} minutes from now.'
    if cmd=='/reminders':
        with connect() as db:rows=db.execute('SELECT id,due,text FROM alerts WHERE uid=? ORDER BY due',(uid,)).fetchall()
        return '\n'.join(f'#{r[0]} in {max(0,math.ceil((r[1]-time.time())/60))} min: {r[2]}' for r in rows) or 'No reminders.'
    if cmd=='/cancel_reminder':
        if len(args)!=1:return 'Use /cancel_reminder ID'
        with connect() as db:db.execute('DELETE FROM alerts WHERE id=? AND uid=?',(int(args[0]),uid))
        return 'Reminder removed if it belonged to you.'
    if cmd in ('/admin','/approve','/remove','/private','/faq_set','/faq_delete','/unanswered'):
        if uid not in owners():return 'Admin access required. Use /myid to get your ID; the owner must set ADMIN_USER_IDS in Railway.'
        if cmd=='/admin':
            with connect() as db:
                usage=dict(db.execute('SELECT kind,count(*) FROM events WHERE stamp>? GROUP BY kind',(time.time()-86400,)).fetchall());ratings=dict(db.execute('SELECT rating,count(*) FROM replies WHERE rating IS NOT NULL GROUP BY rating').fetchall())
            return f'Private access: {setting("private",False)}\n24h calls/events: {usage}\nFeedback: {ratings}\n/approve ID; /remove ID; /private on|off; /faq_set TOPIC | English answer | Hinglish answer; /faq_delete TOPIC; /unanswered'
        if cmd in ('/approve','/remove'):
            if len(args)!=1 or not args[0].isdigit():return 'Use a numeric Telegram user ID.'
            if int(args[0]) in owners():return 'Owner access is controlled by ADMIN_USER_IDS.'
            with connect() as db:db.execute('INSERT OR REPLACE INTO access VALUES (?,?)',(int(args[0]),int(cmd=='/approve')))
            return 'Access updated.'
        if cmd=='/private':
            if args not in (['on'],['off']):return 'Use /private on or /private off.'
            put_setting('private',args==['on']);return 'Private access '+args[0]+'. Owner access is preserved.'
        if cmd=='/faq_set':
            fields=text.partition(' ')[2].split('|')
            if len(fields)!=3 or fields[0].strip() not in FAQ or any(not x.strip() for x in fields):return '/faq_set TOPIC | English answer | Hinglish answer. Topics: '+', '.join(FAQ)
            if any(len(x)>1500 for x in fields):return 'Keep each answer within 1500 characters.'
            put_setting('faq:'+fields[0].strip(),{'en':fields[1].strip(),'hi':fields[2].strip(),'verified_at':time.time(),'source':'owner-approved'})
            return 'FAQ updated; review again within 30 days.'
        if cmd=='/faq_delete':
            if len(args)!=1 or args[0] not in FAQ:return 'Choose a valid FAQ topic.'
            with connect() as db:db.execute('DELETE FROM settings WHERE key=?',('faq:'+args[0],))
            return 'Override removed; verified website summary restored.'
        if cmd=='/unanswered':
            with connect() as db:rows=db.execute('SELECT question FROM issues ORDER BY id DESC LIMIT 10').fetchall()
            return '\n'.join(r[0] for r in rows) or 'No recorded unanswered questions.'
    return None


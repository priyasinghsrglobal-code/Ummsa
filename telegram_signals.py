"""Opt-in Ummsa signal views for existing @srglobal_bot. Paper observations only."""
import time, json, hashlib
import features
from engine import SYMBOLS,TIMEFRAMES
from directional_signal import classify
from web_engine import candidate,step_setup,TERMINAL,VERSION as BREAKOUT
from supply_signal import newest_candidate,VERSION as SUPPLY

STRATEGIES={'direction':'Market Direction','breakout':'Breakout & Retest','supply':'Supply & Demand (Gold 15m)'}
def setup_db(db):
    db.executescript("""CREATE TABLE IF NOT EXISTS signal_subscriptions(uid INTEGER PRIMARY KEY,symbol TEXT,tf TEXT,strategy TEXT,enabled INTEGER,started REAL);
CREATE TABLE IF NOT EXISTS signal_state(symbol TEXT,tf TEXT,strategy TEXT,bar REAL,side TEXT,data TEXT,PRIMARY KEY(symbol,tf,strategy));
CREATE TABLE IF NOT EXISTS signal_delivery(uid INTEGER,event_id TEXT,created REAL,PRIMARY KEY(uid,event_id));""")
def config(uid):
    with features.connect() as db:
        setup_db(db)
        row=db.execute('SELECT * FROM signal_subscriptions WHERE uid=?',(uid,)).fetchone()
        return dict(row) if row else None
def configure(uid,symbol,tf,strategy,enabled):
    if symbol not in SYMBOLS or tf not in TIMEFRAMES or strategy not in STRATEGIES:raise ValueError('Unsupported selection')
    if strategy=='supply' and (symbol,tf)!=('XAUUSD','15m'):raise ValueError('Supply & Demand supports Gold 15m only')
    with features.connect() as db:
        setup_db(db)
        old=db.execute('SELECT * FROM signal_subscriptions WHERE uid=?',(uid,)).fetchone()
        changed=not old or any(old[k]!=v for k,v in [('symbol',symbol),('tf',tf),('strategy',strategy)]) or not old['enabled']
        started=time.time() if changed else old['started']
        db.execute('INSERT OR REPLACE INTO signal_subscriptions VALUES (?,?,?,?,?,?)',(uid,symbol,tf,strategy,int(enabled),started))
def format_signal(result,symbol,tf,strategy,bar):
    """Telegram's compact setup template, with real computed reference levels."""
    side=result.get('side','NEUTRAL')
    tf_label={'5m':'M5','15m':'M15','30m':'M30','1h':'H1','4h':'H4'}.get(tf,tf.upper())
    date=time.strftime('%d %b %H:%M UTC',time.gmtime(bar))
    if side not in ('BUY','SELL'):
        return f'⚪ NEUTRAL\\n{symbol} • {tf_label}\\nNo confirmed direction right now.\\nLast candle: {date}'
    entry=result.get('entry');stop=result.get('stop');targets=result.get('targets') or []
    if not (isinstance(entry,(int,float)) and isinstance(stop,(int,float)) and len(targets)>=2):
        return f'⚪ NEUTRAL\\n{symbol} • {tf_label}\\nNo complete trade levels available.'
    # Only the breakout engine verifies a later retest. Direction mode does not.
    if strategy=='breakout' and result.get('status') in ('Confirmed','TP1 reached','TP2 reached'):
        phrase=('after an '+tf_label+' close above and successful retest.' if side=='BUY'
                else 'after an '+tf_label+' close below and failed retest.')
    elif strategy=='supply':
        phrase='after fresh-zone retest and engulfing candle confirmation.'
    else:
        phrase='after an '+tf_label+' close with '+('bullish' if side=='BUY' else 'bearish')+' indicator alignment.'
    fmt=lambda n:f'{n:.5f}'.rstrip('0').rstrip('.') if abs(n)<10 else f'{n:.2f}'
    mark='🟢' if side=='BUY' else '🔴'
    return (f'{mark} {side} setup\\n'
            f'{symbol} • {tf_label}\\n'
            f'Entry: {fmt(entry)}, {phrase}\\n'
            f'🎯 T1: {fmt(targets[0])}\\n'
            f'🎯 T2: {fmt(targets[1])}\\n'
            f'🛑 SL: {fmt(stop)}\\n'
            f'Last closed candle: {date}\\n'
            'Indicative levels only · No trade execution.')

def calculate(bars,symbol,tf,strategy,previous=None):
    step=TIMEFRAMES[tf][1]
    if strategy=='direction':
        return classify(bars,symbol,tf,step)
    if strategy=='supply':
        setup,_=newest_candidate(bars,symbol,tf,step)
        return setup if setup else {'side':'NEUTRAL'}
    if previous and previous.get('setup'):
        s=previous['setup'];s=dict(s)
        for bar in bars:
            s,_=step_setup(s,bar,step)
        if s['status'] not in TERMINAL:
            return {'side':s['side'] if s['status'] in ('Confirmed','TP1 reached','TP2 reached') else 'NEUTRAL',
                    'entry':s.get('entry'),'stop':s['stop'],'targets':s.get('targets',[]),'setup':s,'status':s['status']}
    s,_=candidate(bars,symbol,tf,step)
    return {'side':'NEUTRAL','setup':s,'status':'Awaiting confirmation' if s else 'No setup'}
def once(symbol,tf,strategy,bars,send_fn=None):
    """First observation only creates a baseline; later *new candle* BUY/SELL transitions alert."""
    close=bars[-1]['t']+TIMEFRAMES[tf][1]
    with features.connect() as db:
        setup_db(db)
        row=db.execute('SELECT * FROM signal_state WHERE symbol=? AND tf=? AND strategy=?',(symbol,tf,strategy)).fetchone()
        previous=json.loads(row['data']) if row and row['data'] else None
        if row and close<=row['bar']:return 'UNCHANGED'
        result=calculate(bars,symbol,tf,strategy,previous)
        side=result['side']
        db.execute('INSERT OR REPLACE INTO signal_state VALUES (?,?,?,?,?,?)',
                   (symbol,tf,strategy,close,side,json.dumps(result)))
        if row is None or side not in ('BUY','SELL') or side==row['side']:return 'BASELINE'
        eid=hashlib.sha256(f'{strategy}:{symbol}:{tf}:{close}:{side}'.encode()).hexdigest()
        targets=result.get('targets') or []
        if not (result.get('entry') and result.get('stop') and len(targets)>=3):return 'NO_LEVELS'
        message=format_signal(result,symbol,tf,strategy,close)
        recipients=[dict(r) for r in db.execute('SELECT * FROM signal_subscriptions WHERE enabled=1 AND symbol=? AND tf=? AND strategy=? AND started<=?',(symbol,tf,strategy,close))]
    if send_fn:
        for sub in recipients:
            if not features.allowed(sub['uid']):continue
            try:
                send_fn(sub['uid'],message)
                with features.connect() as db:
                    setup_db(db)
                    db.execute('INSERT OR IGNORE INTO signal_delivery VALUES (?,?,?)',(sub['uid'],eid,time.time()))
            except Exception:pass
    return 'SIGNAL'
def tasks():
    with features.connect() as db:
        setup_db(db)
        return sorted({(r['symbol'],r['tf'],r['strategy']) for r in db.execute('SELECT * FROM signal_subscriptions WHERE enabled=1') if features.allowed(r['uid'])})

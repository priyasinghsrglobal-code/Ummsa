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
CREATE TABLE IF NOT EXISTS signal_delivery(uid INTEGER,event_id TEXT,created REAL,PRIMARY KEY(uid,event_id));
CREATE TABLE IF NOT EXISTS signal_groups(group_id INTEGER PRIMARY KEY,symbol TEXT NOT NULL,tf TEXT NOT NULL,strategy TEXT NOT NULL,enabled INTEGER NOT NULL,started REAL NOT NULL,last_update REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS signal_group_delivery(group_id INTEGER,event_id TEXT,created REAL,PRIMARY KEY(group_id,event_id));
CREATE TABLE IF NOT EXISTS signal_group_extra(group_id INTEGER NOT NULL,symbol TEXT NOT NULL,tf TEXT NOT NULL,strategy TEXT NOT NULL,enabled INTEGER NOT NULL,started REAL NOT NULL,last_update REAL NOT NULL DEFAULT 0,PRIMARY KEY(group_id,symbol,tf,strategy));
CREATE TABLE IF NOT EXISTS signal_group_trio_seed(group_id INTEGER PRIMARY KEY,seeded REAL NOT NULL);
CREATE TABLE IF NOT EXISTS signal_reminder_delivery(kind TEXT,target INTEGER,event_id TEXT,created REAL,PRIMARY KEY(kind,target,event_id));""")
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
def group_config(group_id):
    with features.connect() as db:
        setup_db(db)
        row=db.execute('SELECT * FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
        return dict(row) if row else None

def configure_group(group_id,symbol='XAUUSD',tf='15m',strategy='direction',enabled=True):
    if not isinstance(group_id,int) or group_id>=0:
        raise ValueError('Group chat ID must be negative.')
    if symbol not in SYMBOLS or tf not in TIMEFRAMES or strategy not in STRATEGIES:
        raise ValueError('Unsupported symbol, timeframe or strategy.')
    if strategy=='supply' and (symbol,tf)!=('XAUUSD','15m'):
        raise ValueError('Supply & Demand supports XAUUSD 15m only.')
    with features.connect() as db:
        setup_db(db)
        old=db.execute('SELECT * FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
        changed=not old or not old['enabled'] or any(old[key]!=v for key,v in (('symbol',symbol),('tf',tf),('strategy',strategy)))
        started=time.time() if changed else old['started']
        last_update=0 if changed else old['last_update']
        db.execute('INSERT OR REPLACE INTO signal_groups VALUES (?,?,?,?,?,?,?)',
                   (group_id,symbol,tf,strategy,int(bool(enabled)),started,last_update))
    return group_config(group_id)

def _validate_group_market(group_id,symbol,tf,strategy):
    if not isinstance(group_id,int) or group_id>=0:
        raise ValueError('Group/channel chat ID must be negative.')
    if symbol not in SYMBOLS or tf not in TIMEFRAMES or strategy not in STRATEGIES:
        raise ValueError('Unsupported symbol, timeframe or strategy.')
    if strategy=='supply' and (symbol,tf)!=('XAUUSD','15m'):
        raise ValueError('Supply & Demand supports XAUUSD 15m only.')


def add_group_market(group_id,symbol,tf='15m',strategy='direction'):
    """Add a market without replacing the original channel and its news settings."""
    _validate_group_market(group_id,symbol,tf,strategy)
    with features.connect() as db:
        setup_db(db)
        main=db.execute('SELECT * FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
        if not main or not main['enabled']:
            raise ValueError('Enable channel alerts first with /channel_on.')
        if (main['symbol'],main['tf'],main['strategy'])==(symbol,tf,strategy):
            return False
        existing=db.execute('SELECT enabled FROM signal_group_extra WHERE group_id=? AND symbol=? AND tf=? AND strategy=?',
                            (group_id,symbol,tf,strategy)).fetchone()
        if existing and existing['enabled']:return False
        now=time.time()
        db.execute('INSERT OR REPLACE INTO signal_group_extra VALUES (?,?,?,?,?,?,0)',
                   (group_id,symbol,tf,strategy,1,now))
        return True


def remove_group_market(group_id,symbol,tf='15m',strategy='direction'):
    _validate_group_market(group_id,symbol,tf,strategy)
    with features.connect() as db:
        setup_db(db)
        main=db.execute('SELECT * FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
        if main and (main['symbol'],main['tf'],main['strategy'])==(symbol,tf,strategy):
            raise ValueError('Primary market cannot be removed. Use /channel_off to turn off all, or /channel_on to change the primary.')
        return bool(db.execute('UPDATE signal_group_extra SET enabled=0 WHERE group_id=? AND symbol=? AND tf=? AND strategy=?',
                        (group_id,symbol,tf,strategy)).rowcount)


def channel_markets(group_id,enabled_only=False):
    with features.connect() as db:
        setup_db(db)
        main=db.execute('SELECT * FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
        extra=[dict(r) for r in db.execute('SELECT * FROM signal_group_extra WHERE group_id=? ORDER BY symbol,tf,strategy',(group_id,))]
    result=[dict(main)] if main else []
    result.extend(extra)
    if enabled_only:
        result=[r for r in result if r['enabled']]
    return result


def seed_gold_silver_bitcoin_channels():
    """One-time additive migration for existing opt-in Gold M15 direction channels.

    Never reenable a market after user removal, /channel_off or /news_off.
    Existing primary subscription remains unchanged. Does not send a historic
    BUY/SELL trade signal at startup.
    """
    with features.connect() as db:
        setup_db(db)
        rows=db.execute("SELECT group_id FROM signal_groups WHERE enabled=1 AND symbol='XAUUSD' AND tf='15m' AND strategy='direction'").fetchall()
        seeded=0
        for row in rows:
            gid=row['group_id']
            if db.execute('SELECT 1 FROM signal_group_trio_seed WHERE group_id=?',(gid,)).fetchone():
                continue
            now=time.time()
            for sym in ('XAGUSD','BTCUSD'):
                db.execute('INSERT OR IGNORE INTO signal_group_extra VALUES (?,?,?,?,?,?,0)',
                           (gid,sym,'15m','direction',1,now))
            db.execute('INSERT INTO signal_group_trio_seed VALUES (?,?)',(gid,now))
            seeded+=1
        return seeded


def disable_group_markets(group_id):
    with features.connect() as db:
        setup_db(db)
        db.execute('UPDATE signal_groups SET enabled=0 WHERE group_id=?',(group_id,))
        db.execute('UPDATE signal_group_extra SET enabled=0 WHERE group_id=?',(group_id,))


def _primary_market(db,group_id):
    row=db.execute('SELECT symbol,tf,strategy FROM signal_groups WHERE group_id=?',(group_id,)).fetchone()
    return (row['symbol'],row['tf'],row['strategy']) if row else None


def enabled_group_markets(db,symbol=None,tf=None,strategy=None):
    """Merge primary and extra channels with one entry per channel/market."""
    criteria=lambda r:(symbol is None or r['symbol']==symbol) and (tf is None or r['tf']==tf) and (strategy is None or r['strategy']==strategy)
    main=[dict(r) for r in db.execute('SELECT * FROM signal_groups WHERE enabled=1')]
    extra=[dict(r) for r in db.execute('SELECT e.* FROM signal_group_extra e JOIN signal_groups g ON e.group_id=g.group_id WHERE e.enabled=1 AND g.enabled=1')]
    unique={}
    for entry in main+extra:
        if criteria(entry):
            unique[(entry['group_id'],entry['symbol'],entry['tf'],entry['strategy'])]=entry
    return list(unique.values())


def format_group_update(result,bars,symbol,tf):
    from engine import indicators,analyze
    i=indicators(bars);ctx=analyze(bars,'Price Action')
    side=result.get('side','NEUTRAL')
    digits=2 if symbol in ('XAUUSD','BTCUSD','ETHUSD') else 3 if symbol in ('XAGUSD','USDJPY','GBPJPY') else 5
    fmt=lambda v:f'{v:.{digits}f}'
    stamp=time.strftime('%d %b %H:%M UTC',time.gmtime(bars[-1]['t']+TIMEFRAMES[tf][1]))
    status='🟢 Bullish view' if side=='BUY' else '🔴 Bearish view' if side=='SELL' else '⚪ Neutral view'
    return (f'📊 Market update | {symbol} {tf.upper()}\n'
            f'{status} | Close: {fmt(bars[-1]["c"])}\n'
            f'RSI: {i["rsi"]:.1f} | Support: {fmt(ctx["support"])} | Resistance: {fmt(ctx["resistance"])}\n'
            f'Candle: {stamp}\n'
            'Closed-candle reference, not a new trade signal.')

def format_signal(result,symbol,tf,strategy,bar):
    """Compact Telegram template; never claim a retest for indicator-only direction."""
    side=result.get('side','NEUTRAL')
    timeframe={'5m':'M5','15m':'M15','30m':'M30','1h':'H1','4h':'H4'}.get(tf,tf.upper())
    date=time.strftime('%d %b %H:%M UTC',time.gmtime(bar))
    if side not in ('BUY','SELL'):
        status=result.get('status')
        qualifier=f' ({status})' if status else ''
        return (f'⚪ NEUTRAL\n{symbol} • {timeframe}\n'
                f'No confirmed BUY/SELL setup{qualifier}.\n'
                f'Last closed candle: {date}')
    entry=result.get('entry');stop=result.get('stop');targets=result.get('targets') or []
    if not (isinstance(entry,(int,float)) and isinstance(stop,(int,float)) and len(targets)>=2):
        return f'⚪ NEUTRAL\n{symbol} • {timeframe}\nNo complete trade levels available.'
    if strategy=='breakout' and result.get('status') in ('Confirmed','TP1 reached','TP2 reached'):
        condition=('after an '+timeframe+' close above and successful retest.' if side=='BUY'
                   else 'after an '+timeframe+' close below and failed retest.')
    elif strategy=='supply':
        condition='after a fresh-zone retest and engulfing confirmation.'
    else:
        condition='after an '+timeframe+' close with '+('bullish' if side=='BUY' else 'bearish')+' indicator alignment.'
    def fmt(n):
        if symbol in ('XAUUSD','BTCUSD','ETHUSD'):return f'{n:.2f}'
        if symbol in ('USDJPY','GBPJPY','XAGUSD'):return f'{n:.3f}'
        return f'{n:.5f}'
    marker='🟢' if side=='BUY' else '🔴'
    return (f'{marker} {side} setup\n'
            f'{symbol} • {timeframe}\n'
            f'Entry: {fmt(entry)}, {condition}\n'
            f'🎯 T1: {fmt(targets[0])}\n'
            f'🎯 T2: {fmt(targets[1])}\n'
            f'🛑 SL: {fmt(stop)}\n'
            f'Last closed candle: {date}\n'
            'Reference levels only. No automatic trade execution.')

def format_analysis(result,bars,symbol,tf,strategy):
    """Add understandable market evidence using the SAME closed candles as the chart."""
    from engine import analyze,indicators
    candle=bars[-1]
    ctx=analyze(bars,'Price Action')
    metrics=indicators(bars)
    digits=2 if symbol in ('XAUUSD','BTCUSD','ETHUSD') else 3 if symbol in ('XAGUSD','USDJPY','GBPJPY') else 5
    fmt=lambda n:f'{n:,.{digits}f}'
    base=format_signal(result,symbol,tf,strategy,candle['t']+TIMEFRAMES[tf][1])
    aligned='Bullish' if candle['c']>metrics['ema20']>metrics['ema50'] else 'Bearish' if candle['c']<metrics['ema20']<metrics['ema50'] else 'Mixed'
    macd='Positive' if metrics['histogram']>0 else 'Negative' if metrics['histogram']<0 else 'Flat'
    details=(f'\n\n📊 Market analysis\n'
             f'Closed price: {fmt(candle["c"])}\n'
             f'Trend / EMAs: {aligned}\n'
             f'EMA20: {fmt(metrics["ema20"])} | EMA50: {fmt(metrics["ema50"])}\n'
             f'RSI (14): {metrics["rsi"]:.1f} | MACD momentum: {macd}\n'
             f'Support: {fmt(ctx["support"])} | Resistance: {fmt(ctx["resistance"])}')
    if result.get('side')=='NEUTRAL':
        details+='\n⏳ Wait: indicators are not aligned for a directional signal. Entry, SL and targets are withheld.'
    elif strategy=='direction':
        details+='\n⚠️ Directional indicator alignment only — a confirmed breakout/retest is not implied.'
    elif result.get('status'):
        details+=f'\nSetup status: {result["status"]}'
    return base+details+'\n\nData: Twelve Data · validated closed candles (not a live executable quote).'

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
def once(symbol,tf,strategy,bars,send_fn=None,group_send_fn=None,private_chart_fn=None):
    """Scan one new closed candle; send signals and periodic group updates.

    Historical data never creates alerts at startup. Delivery is recorded only
    after successful send; failed sends remain visible in server logs.
    """
    close=bars[-1]['t']+TIMEFRAMES[tf][1]
    with features.connect() as db:
        setup_db(db)
        row=db.execute('SELECT * FROM signal_state WHERE symbol=? AND tf=? AND strategy=?',(symbol,tf,strategy)).fetchone()
        previous=json.loads(row['data']) if row and row['data'] else None
        if row and close<=row['bar']:return 'UNCHANGED'
        result=calculate(bars,symbol,tf,strategy,previous)
        side=result.get('side','NEUTRAL')
        db.execute('INSERT OR REPLACE INTO signal_state VALUES (?,?,?,?,?,?)',
                   (symbol,tf,strategy,close,side,json.dumps(result)))
        targets=result.get('targets') or []
        event=bool(row is not None and side in ('BUY','SELL') and side!=row['side'] and
                   result.get('entry') is not None and result.get('stop') is not None and len(targets)>=2)
        eid=hashlib.sha256(f'{strategy}:{symbol}:{tf}:{int(close)}:{side}'.encode()).hexdigest()
        private=[dict(x) for x in db.execute('SELECT * FROM signal_subscriptions WHERE enabled=1 AND symbol=? AND tf=? AND strategy=? AND started<=?',
                     (symbol,tf,strategy,close))] if event else []
        groups=[x for x in enabled_group_markets(db,symbol,tf,strategy) if x['started']<=close]
    if send_fn and event:
        msg=format_signal(result,symbol,tf,strategy,close)
        for sub in private:
            if not features.allowed(sub['uid']):continue
            try:
                with features.connect() as db:
                    setup_db(db)
                    if db.execute('SELECT 1 FROM signal_delivery WHERE uid=? AND event_id=?',(sub['uid'],eid)).fetchone():continue
                send_fn(sub['uid'],msg)
                if private_chart_fn:
                    try:private_chart_fn(sub['uid'],bars,result)
                    except Exception:
                        # The text signal was delivered. A chart failure must not
                        # repeat BUY/SELL alerts or prevent recording delivery.
                        pass
                with features.connect() as db:
                    db.execute('INSERT OR IGNORE INTO signal_delivery VALUES (?,?,?)',(sub['uid'],eid,time.time()))
            except Exception:pass
    if group_send_fn:
        interval=max(3600,int(__import__('os').getenv('GROUP_UPDATE_INTERVAL_SECONDS','14400')))
        for sub in groups:
            should_update=not event and (close-sub['last_update']>=interval)
            if not event and not should_update:continue
            try:
                with features.connect() as db:
                    if event and db.execute('SELECT 1 FROM signal_group_delivery WHERE group_id=? AND event_id=?',(sub['group_id'],eid)).fetchone():
                        continue
                message=(format_signal(result,symbol,tf,strategy,close) if event
                         else format_group_update(result,bars,symbol,tf))
                group_send_fn(sub['group_id'],message,bars,result)
                with features.connect() as db:
                    if event:db.execute('INSERT OR IGNORE INTO signal_group_delivery VALUES (?,?,?)',(sub['group_id'],eid,time.time()))
                    if (sub['symbol'],sub['tf'],sub['strategy'])==_primary_market(db,sub['group_id']):
                        db.execute('UPDATE signal_groups SET last_update=? WHERE group_id=? AND enabled=1',(close,sub['group_id']))
                    else:
                        db.execute('UPDATE signal_group_extra SET last_update=? WHERE group_id=? AND symbol=? AND tf=? AND strategy=? AND enabled=1',
                                   (close,sub['group_id'],symbol,tf,strategy))
            except Exception:pass
    return 'SIGNAL' if event else 'BASELINE' if row is None else 'NO_SIGNAL'

def tasks():
    with features.connect() as db:
        setup_db(db)
        private={(r['symbol'],r['tf'],r['strategy']) for r in db.execute('SELECT * FROM signal_subscriptions WHERE enabled=1') if features.allowed(r['uid'])}
        groups={(r['symbol'],r['tf'],r['strategy']) for r in enabled_group_markets(db)}
        return sorted(private | groups)

def reminder_tick(send_fn,group_send_fn=None,now=None):
    """Opt-in breakout FORMING reminders for the next M15 candle only.

    A confirmed breakout creates a pending retest zone. It cannot promise a
    future confirmation: 15/10/5/1 are countdown to *candle close*, not a
    forecast of the signal time. No fresh market-data requests are made.
    A stage is recorded only after Telegram confirms send; duplicates blocked.
    """
    now=time.time() if now is None else now
    with features.connect() as db:
        setup_db(db)
        rows=[dict(r) for r in db.execute("SELECT * FROM signal_state WHERE strategy='breakout' AND tf='15m'")]
        subscriptions=[dict(r) for r in db.execute("SELECT * FROM signal_subscriptions WHERE enabled=1 AND strategy='breakout' AND tf='15m'")]
        groups=enabled_group_markets(db,tf='15m',strategy='breakout')
    count=0
    for row in rows:
        try:
            result=json.loads(row['data'] or '{}')
            setup=result.get('setup') or {}
            if setup.get('status')!='Awaiting confirmation' or not setup.get('id'):
                continue
            # Only the immediate candle following the confirmed breakout is
            # a countdown candidate. Later pending bars are not predicted.
            if setup.get('created_at')!=row['bar']:
                continue
            end=row['bar']+TIMEFRAMES['15m'][1]
            remaining=end-now
            stage=next((m for m in (15,10,5,1) if m*60-60 < remaining <= m*60),None)
            if stage is None or not (row['bar']<=now<end):
                continue
            event_id=f"{setup['id']}:{int(end)}:{stage}"
            side=setup.get('side','')
            if side not in ('BUY','SELL'):continue
            level=setup.get('level')
            fmt=(f'{level:.2f}' if isinstance(level,(int,float)) else '—')
            close=time.strftime('%H:%M UTC',time.gmtime(end))
            label='🟢' if side=='BUY' else '🔴'
            message=(f'⏳ Potential {side} setup forming — {row["symbol"]} M15\\n'
                     f'{label} Breakout level {fmt}; retest + directional close STILL required.\\n'
                     f'Next candle close in ~{stage} min ({close}).\\n'
                     'WATCH ONLY — not a confirmed signal. It may invalidate or expire.')
            targets=[('private',r['uid']) for r in subscriptions if r['symbol']==row['symbol'] and r['started']<=row['bar'] and features.allowed(r['uid'])]
            if group_send_fn:
                targets.extend(('group',g['group_id']) for g in groups if g['symbol']==row['symbol'] and g['started']<=row['bar'])
            for kind,target in targets:
                with features.connect() as db:
                    setup_db(db)
                    if db.execute('SELECT 1 FROM signal_reminder_delivery WHERE kind=? AND target=? AND event_id=?',
                                  (kind,target,event_id)).fetchone():
                        continue
                try:
                    (send_fn if kind=='private' else group_send_fn)(target,message)
                except Exception:
                    continue
                with features.connect() as db:
                    db.execute('INSERT OR IGNORE INTO signal_reminder_delivery VALUES (?,?,?,?)',
                               (kind,target,event_id,time.time()))
                count+=1
        except (TypeError,ValueError,KeyError):
            continue
    return count


def format_brief(result,bars,symbol,tf,strategy):
    side=result.get('side','NEUTRAL')
    if side in ('BUY','SELL') and result.get('entry') and result.get('stop') and len(result.get('targets') or [])>=2:
        return format_signal(result,symbol,tf,strategy,bars[-1]['t']+TIMEFRAMES[tf][1]).split('Last closed candle:')[0].strip()
    from engine import indicators
    i=indicators(bars)
    price=bars[-1]['c']
    digits=2 if symbol in ('XAUUSD','BTCUSD','ETHUSD') else 3 if symbol in ('XAGUSD','USDJPY','GBPJPY') else 5
    return (f'⚪ NEUTRAL | {symbol} {tf.upper()}\\n'
            f'Price: {price:.{digits}f}\\n'
            f'RSI: {i["rsi"]:.1f} | No aligned BUY/SELL setup.\\n'
            'Wait for confirmation.')

def tradingview_annotations(bars,symbol,tf,result):
    from datetime import datetime,timezone
    side=result.get('side','NEUTRAL')
    def line(price,label,color):
        return {'name':'Horizontal Line','input':{'price':price,'text':label},
                'override':{'lineColor':color,'textColor':color,'fontSize':12}}
    drawings=[]
    if side in ('BUY','SELL') and result.get('entry') and result.get('stop'):
        levels=[('Entry',result['entry'],'rgb(234,179,8)'),('SL',result['stop'],'rgb(239,68,68)')]
        levels.extend((f'T{i+1}',v,'rgb(34,197,94)') for i,v in enumerate((result.get('targets') or [])[:2]))
        drawings=[line(v,n,c) for n,v,c in levels]
    else:
        from engine import analyze
        ctx=analyze(bars,'Price Action')
        drawings=[line(ctx['support'],'Support','rgb(34,197,94)'),line(ctx['resistance'],'Resistance','rgb(239,68,68)')]
    return {'drawings':drawings}

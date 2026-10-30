import json, os, time, logging, signal, threading, re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from engine import SYMBOLS,TIMEFRAMES,DataError,validate,report,detailed_report,compact_report,chart_annotations
from mentor import route, snapshot, TV
import features
import telegram_signals as signals
import channel_content

log=logging.getLogger('sr');logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN',''); KEY=os.environ.get('TWELVE_DATA_API_KEY','')
cache={};cooldowns={}; api_calls=[]; running=True; last_poll=0

def request(url,data=None,headers=None,timeout=40):
    try:
        with urlopen(Request(url,data=data,headers=headers or {}),timeout=timeout) as r:
            return json.loads(r.read(2_000_000))
    except HTTPError as exc:
        raise DataError(f'Provider HTTP {exc.code}; request unavailable.') from None
    except Exception:
        # Never log exceptions containing token-bearing URLs or provider response text.
        raise DataError('Connection unavailable or provider request rejected. Please try again later.') from None

def telegram(method,**params):
    result=request(f'https://api.telegram.org/bot{TOKEN}/{method}',json.dumps(params).encode(),{'Content-Type':'application/json'})
    if not result.get('ok'): raise DataError('Telegram request failed.')
    return result.get('result')

def send(chat,text,buttons=None,reply_to=None,keyboard=None):
    args=dict(chat_id=chat,text=text)
    if keyboard:args['reply_markup']={'keyboard':[[{'text':v} for v in row] for row in keyboard], 'resize_keyboard':True,'one_time_keyboard':True}
    elif buttons:args['reply_markup']={'inline_keyboard':buttons}
    if reply_to:args['reply_parameters']={'message_id':reply_to,'allow_sending_without_reply':True}
    return telegram('sendMessage',**args)

def buttons(items):
    cells=[{'text':label,'callback_data':data} for label,data in items]
    return [cells[i:i+2] for i in range(0,len(cells),2)]

def photo(chat,png,reply_to=None):
    boundary='SRMarketViewUploadBoundary'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n').encode()+png+f'\r\n--{boundary}--\r\n'.encode()
    result=request(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
    if not result.get('ok'):raise DataError('Chart delivery failed. Please retry.')

def candles(symbol,tf):
    k=(symbol,tf); now=time.time()
    if k in cache:
        try:
            bars=validate(cache[k][1],symbol,tf,now)
            if now<bars[-1]['t']+2*TIMEFRAMES[tf][1]:return bars
        except DataError:cache.pop(k,None)
    api_calls[:]=[t for t in api_calls if now-t<60]
    if len(api_calls)>=int(os.getenv('DATA_REQUESTS_PER_MINUTE','6')):
        raise DataError('Market-data capacity reached. Please retry in one minute.')
    api_calls.append(now)
    features.event('market_data_request')
    query=urlencode({'symbol':SYMBOLS[symbol],'interval':TIMEFRAMES[tf][0],'outputsize':300,'timezone':'UTC','apikey':KEY})
    payload=request('https://api.twelvedata.com/time_series?'+query,timeout=20)
    bars=validate(payload,symbol,tf,now);cache[k]=(now,payload)
    return bars

sessions={}
def session(chat):
    now=time.time()
    for k,v in list(sessions.items()):
        if now-v['updated']>3600:sessions.pop(k,None)
    if chat not in sessions:
        if len(sessions)>=1000:sessions.pop(next(iter(sessions)))
        sessions[chat]=features.load_user(chat)
        sessions[chat]['updated']=now
    sessions[chat]['updated']=now
    return sessions[chat]

def chart_veto(text):
    t=text.lower().replace("’", "'")
    return bool(re.search(r"\b(no|not|without|nahi|nhi|nahin|mat|don't|dont)\b|नहीं|मत",t) or
                re.search(r"\b(what is|what are|meaning|kya hai|kya hota|kaise padh|how to read|how do .*read)\b",t))

def wants_chart(text):
    # Common transliterations/typos work without an AI request. Unusual wording
    # is handled by the semantic router, subject to the same veto.
    t=text.lower().strip()
    if chart_veto(t):return False
    noun=r"\b(charts?|chrt|chrat|chatrt|graph|graf|snapshot|screenshot|image|photo|picture|pic|tasveer|tradingview)\b|चार्ट|ग्राफ|तस्वीर"
    request=r"\b(show|send|display|share|draw|plot|see|look|view|want|need|dikha\w*|dekha\w*|dikh\w*|dekhna|bhej\w*|batao|do|dena|de|please|pls|plz|chahi\w*|chaiye|milega|sakta|sakte|sakti)\b|दिखा|भेज|चाहिए"
    if not re.search(noun,t):return False
    if re.search(request,t):return True
    return bool(re.fullmatch(r"(?:gold|silver|bitcoin|ethereum|[a-z]{6}|\d+[mh]|ka|ki|ke|chart|charts|chrt|chrat|graph|graf|tradingview|\s|[?!.,])+",t))


def remember(state,text,reply):
    state['history'].extend([{'role':'user','text':text[:3000]},{'role':'assistant','text':reply[:1500]}])
    state['history']=state['history'][-12:]

def reset_requested(text):
    t=text.lower().strip(' .!?')
    if re.search(r"\b(don't|dont|not|nahi|nhi|mat)\b",t):return False
    return bool(re.fullmatch(r'(?:please )?(?:start fresh(?: topic)?|new topic|reset(?: chat| conversation)?|forget (?:past|previous|old) (?:conversation|chat)|purani (?:baat|chat) bhool jao|naya topic)(?:[ ,;]+(?:forget (?:past|previous|old) (?:conversation|chat)|start fresh(?: topic)?))*',t))

def fast_reply(text,state,message):
    hi=features.language(text,state)=='hi'
    t=text.lower().strip(' .!?')
    if reset_requested(text):
        state['history']=[];state['selection']={};state['pending']=None;state['last_failure']=None
        return 'Bilkul, nayi baat shuru karte hain. Kya discuss karein?' if hi else 'Sure, let’s start fresh. What would you like to discuss?'
    if re.fullmatch(r'(?:hi|hy|hello|hey|hii+|namaste)(?: bhai| bro| dost)?(?:[, ]+(?:kaise ho|kaisa hai|how are you))?',t):
        return 'Hi bhai 👋 Main yahin hoon, help ke liye ready. Tum kaise ho?' if hi else 'Hey 👋 I’m here and ready to help. How are you?'
    why=bool(re.fullmatch(r'(?:kyun|kyu|kyon|why|why is that|kya hua|what happened|what is the problem)',t))
    quoted=message.get('reply_to_message',{}).get('text','')
    failure=state.get('last_failure')
    if why and ((failure and time.time()-failure.get('at',0)<600 and not quoted) or any(x in quoted for x in ('jawab dene mein dikkat','trouble replying','AI service connect'))):
        return 'AI service se jawab nahi aa paaya tha. Aapke message mein koi problem nahi hai. Greetings, SR info aur fresh start phir bhi kaam karte hain.' if hi else 'The AI service could not respond. Nothing is wrong with your message. Greetings, SR information and starting fresh still work.'
    definitions={
        'smc':('SMC mein market structure, liquidity aur price imbalances ko study karte hain. Yeh market samajhne ka framework hai, guaranteed signal nahi.','SMC studies market structure, liquidity and price imbalances. It is an analysis framework, not a guaranteed signal.'),
        'price action':('Price action ka matlab candles, swing highs/lows aur levels par price ki reaction se market samajhna.','Price action means reading candles, swing highs and lows, and how price reacts at key levels.'),
        'stop loss':('Stop loss woh exit level hai jo trade galat jaane par loss limit karne ke liye set karte hain. Fast market mein execution price alag ho sakta hai.','A stop loss is an exit level intended to limit loss when a trade goes against you. Execution can differ in a fast market.')}
    for term,answers in definitions.items():
        if re.fullmatch(r'(?:what is |explain )?'+re.escape(term)+r'(?: kya hai| samjhao)?(?:[, ]+(?:simple mein batao|simply|please))?',t):
            return answers[0 if hi else 1]
    return None

def direct_market(text,state):
    t=text.lower().strip()
    t=re.sub(r'\b(\d+)\s*(?:minutes?|mins?)\b',r'\1m',t)
    t=re.sub(r'\b(\d+)\s*(?:hours?|hrs?)\b',r'\1h',t)
    if state.get('pending') and re.fullmatch(r'\d+',t):
        t={'5':'5m','15':'15m','30':'30m','60':'1h','240':'4h'}.get(t,t)
    sym=next((x for x in SYMBOLS if re.search(r'\b'+x.lower()+r'\b',t)),None)
    for alias,value in [('gold','XAUUSD'),('silver','XAGUSD'),('bitcoin','BTCUSD'),('ethereum','ETHUSD')]:
        if re.search(r'\b'+alias+r'\b',t):sym=value
    match=re.search(r'\b(5m|15m|30m|1h|4h)\b',t)
    tf=match[1] if match else None
    chart=wants_chart(text)
    analysis=bool(re.search(r'\b(analysis|analyse|analyze|outlook|trend|price|levels)\b',t)) and bool(sym or state['selection'].get('symbol'))
    pending=state.get('pending')
    # Only a short slot answer continues the outstanding request.
    slot=bool(re.fullmatch(r'(?:gold|silver|bitcoin|ethereum|[a-z]{6}|\d+(?:[mhdw])?)(?: (?:5m|15m|30m|1h|4h))?',t.strip()))
    if not (chart or analysis or (pending and slot)):return None
    if re.search(r'\b(nasdaq|us30|us100|nifty|banknifty|wti|xtiusd|oil|solusd|solana)\b',t):
        state['pending']=None
        return ('ask','Is symbol ka verified data abhi connected nahi hai. Gold, Silver, forex majors, BTCUSD ya ETHUSD choose karein.')
    requested_tf=re.search(r'\b(\d+(?:m|h|d|w))\b',t)
    if (requested_tf and requested_tf[1] not in TIMEFRAMES) or (pending and re.fullmatch(r'\d+',t)):
        return ('ask','Yeh timeframe supported nahi hai. 5m, 15m, 30m, 1h ya 4h choose karein.')
    if not chart and re.search(r'\b(what is|kya hai|meaning|samjhao|explain)\b',t):return None
    sel=state['selection']
    if sym and sym!=sel.get('symbol'):sel.clear()
    if sym:sel['symbol']=sym
    if tf:sel['timeframe']=tf
    if re.search(r'\bsmc\b',t):sel['method']='SMC'
    elif 'price action' in t:sel['method']='Price Action'
    action='chart' if chart else (pending['action'] if pending and slot else 'analyze')
    state['pending']={'action':action,'question':pending['question'] if pending and slot else text}
    if not sel.get('symbol'):return ('ask','Kaunsa symbol dekhein—Gold, EURUSD ya koi aur?')
    if not sel.get('timeframe'):return ('ask','Kaunsa timeframe—5m, 15m, 30m, 1h ya 4h?')
    question=state['pending']['question'];state['pending']=None
    return (action,question)

def chart_for_chat(chat,symbol,tf):
    state=session(chat)
    state['pending']={'action':'chart','question':f'{symbol} chart dikhao'}
    method=state.get('selection',{}).get('method','Price Action')
    try:
        bars=candles(symbol,tf)
    except DataError:
        send(chat,'Verified candles nahi mile, isliye chart par analysis levels nahi bana sakta. Thodi der baad retry karo.')
        return
    reply=compact_report(bars,symbol,tf,method)
    try:
        photo(chat,snapshot(symbol,tf,annotations=chart_annotations(bars,symbol,tf,method)))
        send(chat,reply+'\nChart: TradingView; levels: Twelve Data. Feeds may differ. Risk box = entry/SL/TP3; lines = TP1/TP2 when a plan exists.')
        state['pending']=None
        remember(state,f'{symbol} {tf} annotated chart',reply)
        log.info('Annotated TradingView image delivered')
    except DataError:
        send(chat,reply+'\nChart annotations load nahi hui; image nahi bheji. Same timeframe bhejkar retry karo.')


def full_analysis_requested(question):
    if question is None:return True
    if re.search(r'\b(short|brief|quick|sirf price|only price)\b',question,re.I):return False
    return bool(re.search(r'\b(analysis|analyse|analyze|outlook|setup|report|detailed|detail)\b',question,re.I))


def analyze_for_chat(chat,symbol,tf,method,question=None):
    state=session(chat);state['selection']={'symbol':symbol,'timeframe':tf,'method':method}
    try:
        bars=candles(symbol,tf)
        if full_analysis_requested(question):
            reply=(detailed_report if question and re.search(r'\b(full detail|detailed report|explain in detail)\b',question,re.I) else compact_report)(bars,symbol,tf,method)
            send(chat,reply,features.feedback_buttons(chat,'analysis'))
            remember(state,question or 'Full market analysis',reply)
            log.info('Detailed computed market report delivered; no generative numeric levels')
            return
        facts=report(bars,symbol,tf,method)
        try:
            simple=(question or '').lower()
            from engine import analyze
            if re.search(r'\b(price|rate|kitne|kitna|levels?|support|resistance|trend)\b',simple) and not re.search(r'\b(why|kyu|kyun|explain|reason|kaise)\b',simple):
                a=analyze(bars,method)
                digits=3 if 'JPY' in symbol or symbol=='XAGUSD' else (2 if symbol in ('XAUUSD','BTCUSD','ETHUSD') else 5)
                reply=f"{symbol} {tf} • Last closed price: {bars[-1]['c']:.{digits}f} (live quote nahi)."
                if re.search(r'\b(trend|levels?|support|resistance)\b',simple):
                    reply+=f"\n{a['trend']} • Support {a['support']:.{digits}f} | Resistance {a['resistance']:.{digits}f}"
            else:
                reply=route(question or 'Give a short current analysis: trend, key levels and conditional scenarios with invalidation.',state['history'],{**state['selection'],'preferences':state.get('preferences',{})},facts=facts)['reply'][:1500]
        except DataError:
            from engine import analyze
            a=analyze(bars,method)
            reply=f"{symbol} {tf}: {a['trend']}. Support {a['support']:.5f}, resistance {a['resistance']:.5f}. Resistance ke upar close aur retest hold ho toh bullish scenario; support ke neeche bearish. Breakout level ke andar close aaye toh scenario invalid."
        from datetime import datetime, timezone
        stamp=datetime.fromtimestamp(bars[-1]['t']+TIMEFRAMES[tf][1],timezone.utc).strftime('%d %b %H:%M UTC')
        reply += f'\nTwelve Data • candle close {stamp}'
        send(chat,reply,features.feedback_buttons(chat,'analysis'))
        state['history'].append({'role':'assistant','text':reply})
        state['history']=state['history'][-12:]
        log.info('Short text market answer delivered; no chart requested')
    except DataError as exc:
        reply='Is symbol ka market data abhi nahi mil raha. Verified candles ke bina analysis nahi de sakta.' if str(exc).startswith('Provider HTTP') else str(exc)
        send(chat,reply);log.warning('Market data unavailable; analysis withheld')

def signal_menu(chat):
    state=session(chat)
    state['selection']={}
    state['pending']='symbol'
    send(chat,'📊 Select symbol',keyboard=[list(SYMBOLS)[i:i+2] for i in range(0,len(SYMBOLS),2)])

def signal_now(chat,symbol,tf,strategy='direction'):
    if symbol not in SYMBOLS or tf not in TIMEFRAMES or strategy not in signals.STRATEGIES:
        send(chat,'Unsupported market or timeframe. /start');return
    try:
        bars=candles(symbol,tf)
        result=signals.calculate(bars,symbol,tf,strategy)
        analysis=signals.format_brief(result,bars,symbol,tf,strategy)
    except (DataError,ValueError) as exc:
        send(chat,'Live candles unavailable: '+str(exc)+' No signal generated.');return
    send(chat,analysis)
    try:
        from mentor import snapshot
        from chart import signal_render
        # TradingView actual chart with the same calculated entry/SL/TP levels.
        image=snapshot(symbol,tf,annotations=signals.tradingview_annotations(bars,symbol,tf,result))
        photo(chat,image)
    except DataError as exc:
        log.warning('TradingView image unavailable: %s',exc)
        try:photo(chat,signal_render(bars,symbol,tf,result))
        except Exception:send(chat,'Chart service temporarily unavailable. Analysis above uses verified closed candles.')
    except Exception:
        log.warning('Chart delivery failed')
        send(chat,'Chart image temporarily unavailable. Analysis above uses verified closed candles.')

def signal_command(chat,text):
    parts=text.split();cmd=parts[0].split('@')[0].lower() if parts else ''
    if cmd=='/signals':
        signal_menu(chat);return True
    if cmd=='/signals_off':
        cfg=signals.config(chat)
        if cfg:signals.configure(chat,cfg['symbol'],cfg['tf'],cfg['strategy'],False)
        send(chat,'Automatic signal alerts OFF. /signal still works.');return True
    if cmd=='/signals_status':
        cfg=signals.config(chat)
        send(chat,('Alerts: '+('ON' if cfg['enabled'] else 'OFF')+f" | {cfg['symbol']} {cfg['tf']} {cfg['strategy']}" if cfg else 'No automatic signals configured. /signals'));return True
    if cmd in ('/signal','/signals_on'):
        if len(parts) not in (3,4):
            send(chat,'Use '+cmd+' XAUUSD 15m direction|breakout|supply');return True
        symbol,tf=parts[1].upper(),parts[2].lower();strategy=parts[3].lower() if len(parts)==4 else 'direction'
        if symbol not in SYMBOLS or tf not in TIMEFRAMES or strategy not in signals.STRATEGIES:
            send(chat,'Unsupported market, timeframe or strategy.');return True
        if strategy=='supply' and (symbol,tf)!=('XAUUSD','15m'):
            send(chat,'Supply & Demand supports Gold 15m only.');return True
        if cmd=='/signals_on':
            signals.configure(chat,symbol,tf,strategy,True)
            send(chat,f'Alerts ON: {symbol} {tf} {strategy}. Only new BUY/SELL transitions notify you. First scan establishes a baseline. No guaranteed trade fills.')
        else:signal_now(chat,symbol,tf,strategy)
        return True
    return False

def handle_inner(update):
    """Reply keyboard sends actual Telegram user messages for symbol and timeframe."""
    q=update.get('callback_query')
    m=q.get('message',{}) if q else update.get('message',{})
    chat=m.get('chat',{}).get('id')
    if not chat or m.get('chat',{}).get('type')!='private':return
    if q:
        telegram('answerCallbackQuery',callback_query_id=q['id'])
        signal_menu(chat);return
    raw=m.get('text','').strip()
    cmd=raw.split()[0].split('@')[0].lower() if raw else ''
    if cmd in ('/start','/menu','/signals','/reset'):
        signal_menu(chat);return
    if cmd in ('/signals_off','/signals_status','/signal','/signals_on'):
        try:signal_command(chat,raw)
        except DataError as exc:send(chat,str(exc))
        return
    if cmd=='/myid':
        send(chat,f'Your Telegram ID: {chat}');return
    state=session(chat)
    symbol=raw.upper()
    if symbol in SYMBOLS:
        state['selection']={'symbol':symbol}
        state['pending']='timeframe'
        send(chat,f'{symbol} selected. Choose timeframe.',keyboard=[list(TIMEFRAMES)[i:i+2] for i in range(0,len(TIMEFRAMES),2)])
        return
    if raw.lower() in TIMEFRAMES and state.get('pending')=='timeframe':
        symbol=state.get('selection',{}).get('symbol')
        if not symbol:
            signal_menu(chat);return
        tf=raw.lower()
        state['selection']['timeframe']=tf
        state['pending']=None
        signal_now(chat,symbol,tf,'direction')
        return
    signal_menu(chat)

def channel_command(update):
    """Channel admins post channel commands; verify the bot can post there.

    Channel posts have no reliable individual user identity. Do NOT treat
    anonymous channel posts as personal account commands.
    """
    m=update.get('channel_post') or {}
    chat_info=m.get('chat') or {}
    channel=chat_info.get('id')
    if chat_info.get('type')!='channel' or not isinstance(channel,int) or channel>=0:
        return
    parts=(m.get('text') or '').strip().split()
    cmd=parts[0].split('@')[0].lower() if parts else ''
    if cmd not in ('/channel_test','/channel_on','/channel_off','/channel_status','/channel_help'):
        return
    log.info('Channel command received: %s',cmd)
    # A channel_post is authored through the channel's publishing rights.
    # Verify this bot itself is an administrator with permission to publish.
    try:
        me=telegram('getMe')
        member=telegram('getChatMember',chat_id=channel,user_id=me['id'])
        if member.get('status') not in ('administrator','creator') or member.get('can_post_messages') is False:
            log.warning('Channel bot is not permitted to post')
            return
    except (DataError,KeyError,TypeError):
        log.warning('Channel administrative permission verification failed')
        return
    if cmd=='/channel_test':
        send(channel,'✅ Channel delivery working. Test message only — no trading signal.')
        return
    if cmd=='/channel_help':
        send(channel,'/channel_on XAUUSD 15m direction — enable automatic updates\n'
                     '/channel_status — current settings\n'
                     '/channel_off — disable updates\n'
                     '/channel_test — check delivery')
        return
    cfg=signals.group_config(channel)
    if cmd=='/channel_status':
        if cfg:
            send(channel,f'Channel broadcasts: {"ON" if cfg["enabled"] else "OFF"} | '
                         f'{cfg["symbol"]} {cfg["tf"]} {cfg["strategy"]}')
        else:send(channel,'Channel broadcasts OFF. Post /channel_on XAUUSD 15m direction.')
        return
    if cmd=='/channel_off':
        if cfg:signals.configure_group(channel,cfg['symbol'],cfg['tf'],cfg['strategy'],False)
        send(channel,'Channel market updates and signals OFF.')
        return
    if len(parts) not in (3,4):
        send(channel,'Use /channel_on XAUUSD 15m direction')
        return
    symbol=parts[1].upper();tf=parts[2].lower()
    strategy=parts[3].lower() if len(parts)==4 else 'direction'
    try:
        signals.configure_group(channel,symbol,tf,strategy,True)
    except ValueError as exc:
        send(channel,str(exc))
        return
    channel_content.set_settings(channel,news=True,engagement=True)
    log.info('Channel broadcasts enabled: %s %s %s',symbol,tf,strategy)
    send(channel,f'✅ Channel alerts ON | {symbol} {tf.upper()} | {strategy}\n'
                 'Automatic BUY/SELL changes and periodic closed-candle market updates.')
    # Send an immediate current snapshot, never a fabricated historical BUY/SELL alert.
    try:
        bars=candles(symbol,tf)
        result=signals.calculate(bars,symbol,tf,strategy)
        send(channel,signals.format_group_update(result,bars,symbol,tf))
        with features.connect() as db:
            signals.setup_db(db)
            db.execute('UPDATE signal_groups SET last_update=? WHERE group_id=? AND enabled=1',
                       (bars[-1]['t']+TIMEFRAMES[tf][1],channel))
        log.info('Channel initial market update delivered')
    except DataError as exc:
        log.warning('Channel initial market data unavailable: %s',str(exc))
        send(channel,'Initial live market data unavailable. Auto-scans remain enabled; '
                     'no signal is issued without verified candles.')
    except Exception:
        log.warning('Channel initial snapshot failed; auto-scans remain configured')
        send(channel,'Initial market update could not load. Scheduled monitoring remains ON.')


def group_admin(chat,uid):
    """Only Telegram-reported creator/administrator may configure group broadcasts."""
    try:
        member=telegram('getChatMember',chat_id=chat,user_id=uid)
        return member.get('status') in ('administrator','creator')
    except DataError:return False

def group_command(update):
    m=update.get('message') or {}
    chat=m.get('chat',{}).get('id');uid=m.get('from',{}).get('id')
    if m.get('chat',{}).get('type') not in ('group','supergroup') or not isinstance(uid,int):return
    parts=m.get('text','').strip().split()
    cmd=parts[0].split('@')[0].lower() if parts else ''
    if cmd not in ('/group_on','/group_off','/group_status','/group_help','/group_test'):return
    log.info('Group command received: %s',cmd)
    if not group_admin(chat,uid):
        log.warning('Group command not authorized or getChatMember unavailable: %s',cmd)
        send(chat,'Group admin verification failed. Make the bot an admin with Post Messages permission, then retry.');return
    if cmd=='/group_test':
        send(chat,'✅ Group message delivery working. This is a test, not a trading signal.');return
    if cmd=='/group_help':
        send(chat,'/group_on XAUUSD 15m direction — turn on alerts and periodic market updates.\n/group_off — stop updates.\n/group_status — view settings.');return
    cfg=signals.group_config(chat)
    if cmd=='/group_status':
        send(chat,(f'Group updates: {"ON" if cfg["enabled"] else "OFF"} | {cfg["symbol"]} {cfg["tf"]} {cfg["strategy"]}' if cfg else 'Group alerts OFF. /group_help'));return
    if cmd=='/group_off':
        if cfg:signals.configure_group(chat,cfg['symbol'],cfg['tf'],cfg['strategy'],False)
        send(chat,'Group signal broadcasts OFF.');return
    if len(parts) not in (3,4):
        send(chat,'Use /group_on XAUUSD 15m direction');return
    symbol=parts[1].upper();tf=parts[2].lower();strategy=parts[3].lower() if len(parts)==4 else 'direction'
    try:signals.configure_group(chat,symbol,tf,strategy,True)
    except ValueError as exc:send(chat,str(exc));return
    log.info('Group broadcasts configured for %s %s %s',symbol,tf,strategy)
    send(chat,f'✅ Group broadcasts ON — {symbol} {tf.upper()} ({strategy}).\nNew BUY/SELL changes + periodic market updates. First scan is a baseline, not a historical alert.')

def group_broadcast(chat,message,bars,result,symbol,tf):
    send(chat,message)
    if result.get('side') not in ('BUY','SELL') or not result.get('entry'):
        return
    try:
        from chart import signal_render
        photo(chat,signal_render(bars,symbol,tf,result))
    except Exception:
        log.warning('Group signal chart unavailable; text signal was delivered')


def handle(update):
    if (update.get('channel_post') or {}).get('chat',{}).get('type')=='channel':
        channel_command(update);return
    q=update.get('callback_query');m=q.get('message',{}) if q else update.get('message',{})
    actor=(q or m).get('from',{})
    uid=actor.get('id');chat=m.get('chat',{}).get('id')
    if m.get('chat',{}).get('type') in ('group','supergroup'):
        group_command(update);return
    # Authorize the sender, including callback queries, before reading personal state.
    if not isinstance(uid,int) or actor.get('is_bot') or m.get('chat',{}).get('type')!='private' or uid!=chat:return
    text=m.get('text','').strip()
    if text.split('@')[0]=='/myid':send(chat,f'Your Telegram user ID: {uid}');return
    if text=='/delete_my_data confirm':
        state=session(chat)
        send(chat,features.command(uid,text,state))
        sessions.pop(chat,None)
        return
    if not features.allowed(uid):
        if q:telegram('answerCallbackQuery',callback_query_id=q['id'],text='Private bot: contact the admin for access.')
        else:send(chat,'Yeh private bot hai. Access ke liye admin se contact karein. Aapki Telegram ID: '+str(uid))
        return
    now=time.time()
    recent=[t for t in cooldowns.get(uid,[]) if now-t<60]
    if len(recent)>=12:
        if q:telegram('answerCallbackQuery',callback_query_id=q['id'],text='Please wait a minute.')
        else:send(chat,'Please wait a minute before sending more requests.')
        return
    cooldowns[uid]=recent+[now]
    if len(cooldowns)>2000:
        for k,v in list(cooldowns.items()):
            if not v or now-v[-1]>60:cooldowns.pop(k,None)
    started=time.monotonic()
    try:handle_inner(update)
    finally:
        log.info("Reply handler completed; elapsed_ms=%s",round((time.monotonic()-started)*1000))
        if chat in sessions:
            features.save_user(chat,sessions[chat])
            if text=='/delete_my_data confirm':
                with features.connect() as db:db.execute('DELETE FROM users WHERE id=?',(chat,))
                sessions.pop(chat,None)

def deliver_reminders():
    # Same polling thread: avoid racing access revocation with a delivery.
    with features.connect() as db:rows=db.execute('SELECT id,uid,text FROM alerts WHERE due<=? ORDER BY due LIMIT 5',(time.time(),)).fetchall()
    for row in rows:
        try:
            if features.allowed(row['uid']):send(row['uid'],'Reminder: '+row['text'])
            with features.connect() as db:db.execute('DELETE FROM alerts WHERE id=?',(row['id'],))
        except DataError:
            with features.connect() as db:db.execute('UPDATE alerts SET due=? WHERE id=?',(time.time()+300,row['id']))


def conversation_probe():
    try:
        route('Say hello briefly.',[],{})
        log.info('AI conversation startup check passed')
    except DataError:
        log.warning('AI conversation startup check unavailable; local conversation features remain ready')

def service_probe():
    # One bounded startup probe per service; never sends Telegram messages.
    for label,probe in [('Gemini',lambda: route('Say hello briefly.',[],{})),('TradingView',lambda: snapshot('EURUSD','5m')),('Market data',lambda: candles('EURUSD','5m'))]:
        try:
            probe();log.info('%s startup check passed',label)
        except DataError as exc:
            log.warning('%s startup check failed: %s',label,str(exc))
        except Exception:
            log.warning('%s startup check failed; details suppressed',label)

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy=bool(last_poll and time.time()-last_poll<120)
        self.send_response(200 if healthy else 503);self.end_headers();self.wfile.write(b'OK' if healthy else b'NOT_READY')
    def log_message(self,*args):pass

def main():
    global running,last_poll
    if not TOKEN or not KEY:
        log.error('Required secure variables missing: TELEGRAM_BOT_TOKEN / TWELVE_DATA_API_KEY');return 1
    with features.connect() as db:db.execute('SELECT 1')
    log.info('Signal bot: persistent storage ready; admin configured=%s',bool(features.owners()))
    me=telegram('getMe')
    if me.get('username','').lower()!='srglobal_bot':
        log.error('Token does not belong to expected bot @srglobal_bot');return 1
    if telegram('getWebhookInfo').get('url'):
        log.error('Existing webhook detected. Remove it deliberately before enabling this polling worker.');return 1
    threading.Thread(target=HTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Health).serve_forever,daemon=True).start()
    def stop(*_):
        global running
        running=False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    if os.getenv('STARTUP_PROBE','false').lower()=='true':
        threading.Thread(target=service_probe,daemon=True).start()
    last_poll=time.time()  # Allow rollout healthcheck before old polling worker drains.
    offset=0;next_signal_scan=0;next_content_scan=0;signal_cursor=0;log.info('Signal bot polling started')
    while running:
        try:
            updates=telegram('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query','channel_post'])
            last_poll=time.time()
            deliver_reminders()
            if time.time()>=next_content_scan:
                next_content_scan=time.time()+1800
                try:channel_content.tick(telegram,signals)
                except Exception:log.warning('Channel content job failed; retry next cycle')
            if time.time()>=next_signal_scan:
                next_signal_scan=time.time()+max(90,int(os.getenv('TELEGRAM_SIGNAL_SCAN_SECONDS','90')))
                work=signals.tasks()
                if work:
                    symbol,tf,strategy=work[signal_cursor%len(work)];signal_cursor+=1
                    try:signals.once(symbol,tf,strategy,candles(symbol,tf),send,lambda chat,msg,bars,result: group_broadcast(chat,msg,bars,result,symbol,tf))
                    except DataError as exc:log.warning('Signal scan paused: %s',str(exc))
                    except Exception:log.warning('Signal scan failed; retry at next interval')
            for update in updates:
                try:handle(update)
                except Exception:
                    log.warning('Update handling failed; sensitive details suppressed')
                    msg=update.get('message') or update.get('callback_query',{}).get('message',{})
                    if msg.get('chat',{}).get('type')=='private':
                        try:send(msg['chat']['id'],'Reply process nahi ho paaya. Ek baar dobara try karo.')
                        except Exception:pass
                offset=update['update_id']+1
        except DataError as exc:
            log.warning('Polling unavailable: %s',str(exc));time.sleep(5)
        except Exception:
            log.warning('Polling unavailable; retrying');time.sleep(5)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        log.error('Startup failed; check credentials and service connectivity');raise SystemExit(1)





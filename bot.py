import json, os, time, logging, signal, threading, re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from engine import SYMBOLS,TIMEFRAMES,DataError,validate,report
from mentor import route, snapshot, TV
import features

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

def send(chat,text,buttons=None):
    args=dict(chat_id=chat,text=text)
    if buttons:args['reply_markup']={'inline_keyboard':buttons}
    return telegram('sendMessage',**args)

def buttons(items):
    cells=[{'text':label,'callback_data':data} for label,data in items]
    return [cells[i:i+2] for i in range(0,len(cells),2)]

def photo(chat,png):
    boundary='SRMarketViewUploadBoundary'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n').encode()+png+f'\r\n--{boundary}--\r\n'.encode()
    result=request(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
    if not result.get('ok'):raise DataError('Chart delivery failed. Please retry.')

def candles(symbol,tf):
    k=(symbol,tf); now=time.time()
    if k in cache and now-cache[k][0]<30:
        return validate(cache[k][1],symbol,tf,now)
    api_calls[:]=[t for t in api_calls if now-t<60]
    if len(api_calls)>=int(os.getenv('DATA_REQUESTS_PER_MINUTE','6')):
        raise DataError('Market-data capacity reached. Please retry in one minute.')
    api_calls.append(now)
    features.event('market_data_request')
    query=urlencode({'symbol':SYMBOLS[symbol],'interval':TIMEFRAMES[tf][0],'outputsize':120,'timezone':'UTC','apikey':KEY})
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

def wants_chart(text):
    lower=text.lower()
    if re.search(r"\b(no|not|without|nahi|nhi|mat|don't|dont)\b|नहीं|मत",lower):return False
    # Explicit image intent, not merely a reference to a chart/concept.
    return bool(re.search(r'\b(chart|graph|snapshot|image|photo)\b|चार्ट',lower) and re.search(r'\b(show|send|display|dikhao|dikha|bhejo|bhej|do|please|pls|chahiye)\b|दिखाओ|भेजो',lower))

def chart_for_chat(chat,symbol,tf):
    try:
        photo(chat,snapshot(symbol,tf))
        send(chat,f'{TV[symbol]} • {tf} • TradingView via CHART-IMG. Snapshot; feed analysis se differ kar sakti hai.')
        log.info('Requested TradingView image delivered')
    except DataError as exc:send(chat,str(exc))

def analyze_for_chat(chat,symbol,tf,method,question=None):
    state=session(chat);state['selection']={'symbol':symbol,'timeframe':tf,'method':method}
    try:
        bars=candles(symbol,tf)
        facts=report(bars,symbol,tf,method)
        try:
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
        send(chat,str(exc));log.warning('Market data unavailable; analysis withheld')

def handle_inner(update):
    q=update.get('callback_query');m=q.get('message',{}) if q else update.get('message',{})
    chat=m.get('chat',{}).get('id')
    if not chat:return
    if m.get('chat',{}).get('type')!='private':
        if q:telegram('answerCallbackQuery',callback_query_id=q['id'],text='Open the bot in a private chat.')
        return
    state=session(chat)
    if not q:
        text=m.get('text','').strip()
        cmd=text.split()[0].split('@')[0].lower() if text else ''
        if cmd in ('/start','/reset'):
            if cmd=='/reset':
                state['history']=[];state['selection']={};state['preferences']={}
            send(chat,features.WELCOME if cmd=='/start' else 'Conversation aur preferences clear ho gaye. Journal aur watchlist retained hain; /delete_my_data se sab erase kar sakte hain.')
            return
        if cmd=='/menu':
            send(chat,'Select symbol',buttons([(v,'s:'+v) for v in SYMBOLS]));return
        if cmd=='/privacy':
            send(chat,'Recent conversation, preferences and journal are saved privately for your Telegram ID. AI requests include recent context and preferences. Failed questions may be visible to the bot owner for 7 days; never send passwords or ID documents. /reset clears context; /delete_my_data confirm erases saved personal data.');return
        try:
            result=features.command(chat,text,state)
            if result is None and text:
                result=features.numbers_review(text)
            if result is not None:
                send(chat,result);return
        except (ValueError,OverflowError):
            send(chat,'Input format check karein. Prices/amounts finite positive numbers hone chahiye; command without values se example milega.');return
        if text.startswith('/'):
            send(chat,'Command recognise nahi hua. /help mein available options hain.');return
        if re.fullmatch(r'(hi|hy|hello|hey|namaste|hii+)[! .👋]*',text.lower()):
            send(chat,'Hi 👋 Kaise help karun?' if features.language(text,state)=='hi' else 'Hi 👋 How can I help?');return
        topic=features.faq_topic(text)
        if topic:
            reply=features.faq_answer(topic,state,text)
            send(chat,reply,features.feedback_buttons(chat,'sr_faq'))
            state['history'].extend([{'role':'user','text':text[:3000]},{'role':'assistant','text':reply}]);return
        if not text:
            send(chat,'Abhi text mein baat kar sakte hain. Apna sawaal type kar do—chart main khud fetch karunga.');return
        telegram('sendChatAction',chat_id=chat,action='typing')
        try:
            answer=route(text,state['history'],{**state['selection'],'preferences':state.get('preferences',{})})
            state['history'].append({'role':'user','text':text[:3000]})
            if answer.get('symbol') and answer['symbol']!=state['selection'].get('symbol'):
                state['selection']={}
            for key in ('symbol','timeframe','method'):
                if answer.get(key):state['selection'][key]=answer[key]
            if answer['action'] in ('analyze','chart') or wants_chart(text):
                sel=state['selection']
                if not sel.get('symbol'):reply='Kaunsa symbol dekhna hai—Gold, EURUSD ya koi aur?'
                elif not sel.get('timeframe'):reply='Kaunsa timeframe dekhein—5m, 15m, 30m, 1h ya 4h?'
                else:
                    if wants_chart(text):
                        chart_for_chat(chat,sel['symbol'],sel['timeframe'])
                    else:
                        analyze_for_chat(chat,sel['symbol'],sel['timeframe'],sel.get('method','Price Action'),question=text)
                    return
            else:reply=answer['reply'][:1500]
            send(chat,reply or 'Apna sawaal thoda aur detail mein bataiye?',features.feedback_buttons(chat,'conversation'))
            state['history'].append({'role':'assistant','text':reply});state['history']=state['history'][-12:]
            log.info('AI conversation reply delivered')
        except DataError as exc:
            features.issue(chat,text)
            features.event('ai_failure')
            send(chat,str(exc)+' SR help ke liye /sr, aur market selection ke liye /menu available hai.')
            log.warning('AI conversation unavailable')
        return
    telegram('answerCallbackQuery',callback_query_id=q['id'])
    if q.get('data','').startswith('fb:'):
        send(chat,features.feedback(chat,q['data']));return
    parts=q.get('data','').split(':')
    if len(parts)==2 and parts[0]=='s' and parts[1] in SYMBOLS:
        state['selection']={'symbol':parts[1]}
        send(chat,'Select timeframe',buttons([(tf,f't:{parts[1]}:{tf}') for tf in TIMEFRAMES]));return
    if len(parts)==3 and parts[0]=='t' and parts[1] in SYMBOLS and parts[2] in TIMEFRAMES:
        state['selection']={'symbol':parts[1],'timeframe':parts[2]}
        send(chat,'Select analysis method',buttons([(v,f'a:{parts[1]}:{parts[2]}:{v}') for v in ('SMC','Price Action')]));return
    if len(parts)==4 and parts[0]=='a' and parts[1] in SYMBOLS and parts[2] in TIMEFRAMES and parts[3] in ('SMC','Price Action'):
        analyze_for_chat(chat,*parts[1:]);return
    send(chat,'Selection samajh nahi aaya. /start se dobara shuru karein?')

def handle(update):
    q=update.get('callback_query');m=q.get('message',{}) if q else update.get('message',{})
    actor=(q or m).get('from',{})
    uid=actor.get('id');chat=m.get('chat',{}).get('id')
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
    try:handle_inner(update)
    finally:
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
    log.info('SR assistant v2: persistent storage ready; admin configured=%s',bool(features.owners()))
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
    offset=0;log.info('SR Market View polling started')
    while running:
        try:
            updates=telegram('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query'])
            last_poll=time.time()
            deliver_reminders()
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


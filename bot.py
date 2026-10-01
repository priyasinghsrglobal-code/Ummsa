import json, os, time, logging, signal, threading
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from engine import SYMBOLS,TIMEFRAMES,DataError,validate,report
from chart import render

log=logging.getLogger('sr');logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN',''); KEY=os.environ.get('TWELVE_DATA_API_KEY','')
cache={};cooldowns={}; api_calls=[]; running=True; last_poll=0

def request(url,data=None,headers=None,timeout=40):
    try:
        with urlopen(Request(url,data=data,headers=headers or {}),timeout=timeout) as r:
            return json.loads(r.read(2_000_000))
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
    telegram('sendMessage',**args)

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
    query=urlencode({'symbol':SYMBOLS[symbol],'interval':TIMEFRAMES[tf][0],'outputsize':120,'timezone':'UTC','apikey':KEY})
    payload=request('https://api.twelvedata.com/time_series?'+query,timeout=20)
    bars=validate(payload,symbol,tf,now);cache[k]=(now,payload)
    return bars

def handle(update):
    q=update.get('callback_query');m=q.get('message',{}) if q else update.get('message',{})
    chat=m.get('chat',{}).get('id')
    if not chat:return
    if m.get('chat',{}).get('type')!='private':
        if q:telegram('answerCallbackQuery',callback_query_id=q['id'],text='Open the bot in a private chat.')
        return
    if not q:
        send(chat,'SR Market View\nSelect a symbol. Analysis uses provider candles; screenshot uploads are not required.',buttons([(s,'s:'+s) for s in SYMBOLS]))
        return
    telegram('answerCallbackQuery',callback_query_id=q['id'])
    parts=q.get('data','').split(':')
    if len(parts)==2 and parts[0]=='s' and parts[1] in SYMBOLS:
        send(chat,'Select timeframe',buttons([(tf,f't:{parts[1]}:{tf}') for tf in TIMEFRAMES]));return
    if len(parts)==3 and parts[0]=='t' and parts[1] in SYMBOLS and parts[2] in TIMEFRAMES:
        send(chat,'Select analysis method',buttons([(v,f'a:{parts[1]}:{parts[2]}:{v}') for v in ('SMC','Price Action')]));return
    if len(parts)!=4 or parts[0]!='a' or parts[1] not in SYMBOLS or parts[2] not in TIMEFRAMES or parts[3] not in ('SMC','Price Action'):
        send(chat,'Invalid selection. Send /start to begin.');return
    now=time.time();cooldowns_copy=list(cooldowns.items())
    for user,t in cooldowns_copy:
        if now-t>60:cooldowns.pop(user,None)
    if now-cooldowns.get(chat,0)<15:
        send(chat,'Please wait 15 seconds between analyses.');return
    cooldowns[chat]=now
    _,symbol,tf,method=parts
    try:
        bars=candles(symbol,tf)
        png=render(bars,symbol,tf,method)
        photo(chat,png)
        send(chat,report(bars,symbol,tf,method),buttons([('New analysis','s:'+symbol)]))
    except DataError as exc:send(chat,str(exc))

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy=bool(last_poll and time.time()-last_poll<120)
        self.send_response(200 if healthy else 503);self.end_headers();self.wfile.write(b'OK' if healthy else b'NOT_READY')
    def log_message(self,*args):pass

def main():
    global running,last_poll
    if not TOKEN or not KEY:
        log.error('Required secure variables missing: TELEGRAM_BOT_TOKEN / TWELVE_DATA_API_KEY');return 1
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
    offset=0;log.info('SR Market View polling started')
    while running:
        try:
            updates=telegram('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query'])
            last_poll=time.time()
            for update in updates:
                try:handle(update)
                except Exception:log.warning('Update handling failed; sensitive details suppressed')
                offset=update['update_id']+1
        except Exception:
            log.warning('Polling unavailable; retrying');time.sleep(5)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        log.error('Startup failed; check credentials and service connectivity');raise SystemExit(1)

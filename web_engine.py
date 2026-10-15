"""Chronological, closed-candle breakout/retest lifecycle. No orders or AI decisions."""
import hashlib
from engine import analyze, indicators

VERSION = 'breakout-retest-v1'
RULES = {'lookback': 20, 'atr_buffer': .1, 'retest_atr': .15, 'expiry_bars': 8,
         'max_active_bars': 32, 'min_rr': 2, 'max_atr_fraction': .03}
TERMINAL = {'Invalidated', 'Expired', 'Target reached', 'Stop-loss reached', 'Ambiguous'}

def step_setup(setup, bar, step):
    """Never uses future bars; confirmation candle cannot also be an outcome candle."""
    s = dict(setup)
    if s['status'] in TERMINAL or bar['t'] <= s['last_bar']: return s, None
    s['last_bar'] = bar['t']
    old = s['status']; side = s['side']; level = s['level']
    if old == 'Awaiting confirmation':
        if bar['t'] >= s['expires_at']:
            s['status'] = 'Expired'; s['reason'] = 'Retest window expired.'
        elif (bar['c'] < level if side == 'BUY' else bar['c'] > level):
            s['status'] = 'Invalidated'; s['reason'] = 'Closed back across the frozen breakout level.'
        elif (bar['l'] <= level+s['tolerance'] and bar['c'] > level and bar['c'] > bar['o'] if side == 'BUY'
              else bar['h'] >= level-s['tolerance'] and bar['c'] < level and bar['c'] < bar['o']):
            entry=bar['c']; risk=(entry-s['stop']) if side=='BUY' else (s['stop']-entry)
            if risk <= 0:
                s['status']='Invalidated';s['reason']='Invalid risk geometry at confirmation.'
            else:
                sign=1 if side=='BUY' else -1
                targets=[entry+sign*risk*n for n in (1,2,3)]
                if min(targets)<=0:
                    s['status']='Invalidated';s['reason']='Target geometry is invalid.'
                else:
                    s.update(status='Confirmed',entry=entry,targets=targets,confirmed_at=bar['t']+step,
                             confirmation_bar=bar['t'],reason='Breakout followed by a later retest and directional close.',
                             rr=2,expires_at=bar['t']+step*RULES['max_active_bars'])
    elif old in ('Confirmed','TP1 reached','TP2 reached'):
        # OHLC cannot tell which came first if a target and stop were touched.
        stop_hit=bar['l']<=s['stop'] if side=='BUY' else bar['h']>=s['stop']
        hit=[n+1 for n,t in enumerate(s['targets']) if (bar['h']>=t if side=='BUY' else bar['l']<=t)]
        new_hit=max(hit,default=0)>s.get('target_hit',0)
        if stop_hit and new_hit:
            s['status']='Ambiguous';s['reason']='Stop and a new target touched in the same candle; sequence unknown.'
        elif stop_hit:
            s['status']='Stop-loss reached';s['reason']='Stop touched in monitored candle data; execution not verified.'
        elif new_hit:
            s['target_hit']=max(hit);s['status']='Target reached' if max(hit)==3 else f'TP{max(hit)} reached'
            s['reason']='Target touched in monitored candle data; execution not verified.'
        elif bar['t']>=s['expires_at']:
            s['status']='Expired';s['reason']='Monitoring window expired.'
    event = {'status':s['status'],'timestamp':bar['t']+step,'reason':s['reason']} if s['status']!=old else None
    return s,event

def candidate(bars,symbol,tf,step):
    if len(bars)<60:return None,'At least 60 closed candles are required.'
    ind=indicators(bars);last=bars[-1];prev=bars[-2];prior=bars[-21:-1]
    high=max(b['h'] for b in prior);low=min(b['l'] for b in prior);atr=ind['atr']
    if atr<=0 or atr/last['c']>RULES['max_atr_fraction']:return None,'Volatility outside configured limits.'
    buy=last['c']>high and prev['c']<=high and last['c']>ind['ema20']>ind['ema50'] and ind['rsi']>=55
    sell=last['c']<low and prev['c']>=low and last['c']<ind['ema20']<ind['ema50'] and ind['rsi']<=45
    if not (buy or sell):return None,'Waiting for a 20-bar range breakout with EMA20/50 and RSI alignment.'
    side='BUY' if buy else 'SELL';level=high if buy else low
    stop=low-RULES['atr_buffer']*atr if buy else high+RULES['atr_buffer']*atr
    if stop<=0:return None,'Invalid stop geometry.'
    uid=hashlib.sha256(f'{VERSION}:{symbol}:{tf}:{side}:{last["t"]}'.encode()).hexdigest()[:20]
    return dict(id=uid,symbol=symbol,timeframe=tf,side=side,status='Awaiting confirmation',level=level,
                entry=None,stop=stop,targets=[],target_hit=0,atr=atr,tolerance=RULES['retest_atr']*atr,
                created_at=last['t']+step,last_bar=last['t'],expires_at=last['t']+step*(RULES['expiry_bars']+1),
                strategy=VERSION,reason='Breakout closed. Waiting for a later retest and directional close.'),None

def process(bars,symbol,tf,step,existing=None):
    """Replay existing state without moving its levels. Never backfill an old confirmed entry."""
    s=existing;events=[]
    if s:
        for bar in bars:
            s,event=step_setup(s,bar,step)
            if event:events.append(event)
    if s and s['status'] not in TERMINAL:return s,events,None
    new,reason=candidate(bars,symbol,tf,step)
    if new and (not s or new['id']!=s['id']):
        return new,[{'status':new['status'],'timestamp':new['created_at'],'reason':new['reason']}],None
    return s,events,reason

def context(bars):
    a=analyze(bars,'SMC');i=indicators(bars)
    return {'price':bars[-1]['c'],'support':a['support'],'resistance':a['resistance'],'trend':a['trend'],
            'atr':i['atr'],'rsi':i['rsi'],'ema20':i['ema20'],'ema50':i['ema50'],'facts':a['facts']}

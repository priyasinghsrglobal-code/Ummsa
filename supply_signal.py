"""Independent rule-based Supply/Demand candle scanner. NO broker orders.

This numeric variant is an explicitly chosen interpretation, NOT a set of
parameters verified by the uploaded tutorial. Newest closed candle only.
No historical alerts are fabricated at startup.
"""
import hashlib

VERSION = 'supply-demand-v1-observational'
RULES = {
 'symbol':'XAUUSD','timeframe':'15m','base_candles':3,
 'max_base_atr':1.20,'impulse_body_atr':1.25,'impulse_clearance_atr':0.15,
 'max_zone_age_bars':50,'min_zone_age_bars':3,'stop_buffer_atr':0.15,
 'max_risk_atr':3.0,'max_monitor_bars':32,'first_retest_only':True,
 'confirmation':'directional body engulfing + close beyond zone',
 'targets_R':[1,2,3],
}

def atr_pre(bars,i,n=14):
    """ATR uses n earlier candles; never depends on the confirmation candle."""
    if i<n+1:return None
    values=[]
    for k in range(i-n,i):
        b,p=bars[k],bars[k-1]
        values.append(max(b['h']-b['l'],abs(b['h']-p['c']),abs(b['l']-p['c'])))
    return sum(values)/n

def engulf(prev,bar,side):
    if side=='BUY':
        return prev['c']<prev['o'] and bar['c']>bar['o'] and bar['o']<=prev['c'] and bar['c']>=prev['o']
    return prev['c']>prev['o'] and bar['c']<bar['o'] and bar['o']>=prev['c'] and bar['c']<=prev['o']

def newest_candidate(bars,symbol,tf,step):
    """Only newest closed candle may confirm a trade observation."""
    if symbol!=RULES['symbol'] or tf!=RULES['timeframe']:
        return None,'Supply/Demand live observation currently supports Gold 15m only.'
    if len(bars)<60:return None,'At least 60 validated closed candles required.'
    bar,prev=bars[-1],bars[-2]
    i=len(bars)-1
    candidates=[]
    first=max(16,i-RULES['max_zone_age_bars']-RULES['base_candles'])
    for start in range(first,i-RULES['base_candles']-RULES['min_zone_age_bars']+1):
        impulse_index=start+RULES['base_candles']
        base=bars[start:impulse_index];impulse=bars[impulse_index]
        base_atr=atr_pre(bars,impulse_index)
        if not base_atr or base_atr<=0:continue
        lo=min(b['l'] for b in base);hi=max(b['h'] for b in base)
        if lo<=0 or hi<=lo or hi-lo>RULES['max_base_atr']*base_atr:continue
        if abs(impulse['c']-impulse['o'])<RULES['impulse_body_atr']*base_atr:continue
        if impulse['c']>impulse['o'] and impulse['c']>hi+RULES['impulse_clearance_atr']*base_atr:side='BUY'
        elif impulse['c']<impulse['o'] and impulse['c']<lo-RULES['impulse_clearance_atr']*base_atr:side='SELL'
        else:continue
        if any(b['l']<=hi and b['h']>=lo for b in bars[impulse_index+1:-1]):continue
        if not (bar['l']<=hi and bar['h']>=lo):continue
        if not engulf(prev,bar,side):continue
        if side=='BUY' and bar['c']<=hi:continue
        if side=='SELL' and bar['c']>=lo:continue
        atr=atr_pre(bars,i)
        if not atr or atr<=0:continue
        stop=lo-RULES['stop_buffer_atr']*atr if side=='BUY' else hi+RULES['stop_buffer_atr']*atr
        entry=bar['c'];risk=entry-stop if side=='BUY' else stop-entry
        if stop<=0 or risk<=0 or risk>RULES['max_risk_atr']*atr:continue
        sign=1 if side=='BUY' else -1
        targets=[entry+sign*risk*r for r in RULES['targets_R']]
        if not all(t>0 for t in targets):continue
        uid=hashlib.sha256(f'{VERSION}:{symbol}:{tf}:{side}:{bars[start]["t"]}:{bar["t"]}'.encode()).hexdigest()[:20]
        candidates.append(dict(
            id=uid,symbol=symbol,timeframe=tf,side=side,status='Confirmed',
            level=hi if side=='BUY' else lo,zone_low=lo,zone_high=hi,
            zone_created_at=impulse['t']+step,zone_base_time=bars[start]['t'],
            zone_type='DEMAND' if side=='BUY' else 'SUPPLY',
            entry=entry,stop=stop,targets=targets,target_hit=0,rr=2,
            strategy=VERSION,created_at=bar['t']+step,
            confirmed_at=bar['t']+step,confirmation_bar=bar['t'],
            last_bar=bar['t'],expires_at=bar['t']+step*(RULES['max_monitor_bars']+1),
            reason='First return to fresh zone; engulfing closed beyond zone. Paper observation; execution not verified.'
        ))
    if not candidates:
        return None,'No fresh zone first-retest + engulfing confirmation on the latest closed candle.'
    return max(candidates,key=lambda s:s['zone_created_at']),None

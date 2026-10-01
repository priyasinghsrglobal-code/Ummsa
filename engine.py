"""Deterministic closed-candle analysis. No LLM and no fabricated fallback."""
from datetime import datetime, timezone
import math

SYMBOLS = {'XAUUSD':'XAU/USD','XAGUSD':'XAG/USD','EURUSD':'EUR/USD','GBPUSD':'GBP/USD','USDJPY':'USD/JPY','GBPJPY':'GBP/JPY','BTCUSD':'BTC/USD','ETHUSD':'ETH/USD'}
TIMEFRAMES = {'5m':('5min',300),'15m':('15min',900),'30m':('30min',1800),'1h':('1h',3600),'4h':('4h',14400)}
class DataError(Exception): pass

def validate(payload, symbol, tf, now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    if payload.get('status') == 'error' or not payload.get('values'):
        raise DataError('Market data unavailable, API quota exhausted, or symbol not enabled. No analysis generated.')
    meta = payload.get('meta', {})
    if meta.get('symbol') != SYMBOLS[symbol] or meta.get('interval') != TIMEFRAMES[tf][0]:
        raise DataError('Provider symbol/timeframe mismatch. No analysis generated.')
    step = TIMEFRAMES[tf][1]
    bars=[]
    try:
        for row in payload['values']:
            t=datetime.fromisoformat(row['datetime']).replace(tzinfo=timezone.utc).timestamp()
            o,h,l,c=(float(row[k]) for k in ('open','high','low','close'))
            if not all(math.isfinite(v) and v>0 for v in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError()
            if t>now+60: raise ValueError()
            if t+step<=now: bars.append({'t':t,'o':o,'h':h,'l':l,'c':c})
    except (KeyError,ValueError,TypeError):
        raise DataError('Invalid candle values or timestamps. No analysis generated.') from None
    bars.sort(key=lambda b:b['t'])
    if len(bars)<60: raise DataError('Fewer than 60 closed candles available. No analysis generated.')
    if any(b['t']<=a['t'] for a,b in zip(bars,bars[1:])):
        raise DataError('Duplicate candle timestamps. No analysis generated.')
    age=now-(bars[-1]['t']+step)
    if age>step+120:
        stamp=datetime.fromtimestamp(bars[-1]['t']+step,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        raise DataError(f'Data stale or market closed. Last candle closed: {stamp}. No current analysis generated.')
    # Reject unexplained gaps; a weekend closure can span Friday through Sunday/Monday.
    for a,b in zip(bars,bars[1:]):
        gap=b['t']-a['t']
        if gap>step*1.5:
            weekend=(symbol not in ('BTCUSD','ETHUSD') and gap<=3*86400 and datetime.fromtimestamp(a['t'],timezone.utc).weekday()==4 and datetime.fromtimestamp(b['t'],timezone.utc).weekday() in (6,0))
            if not weekend: raise DataError('Incomplete candle history (gap detected). No analysis generated.')
    return bars

def analyze(bars, method):
    highs=[]; lows=[]
    for i in range(2,len(bars)-2):
        window=bars[i-2:i+3]
        if all(bars[i]['h']>b['h'] for j,b in enumerate(window) if j!=2): highs.append(bars[i]['h'])
        if all(bars[i]['l']<b['l'] for j,b in enumerate(window) if j!=2): lows.append(bars[i]['l'])
    trend='Range / unclear structure'
    if len(highs)>=2 and len(lows)>=2:
        if highs[-1]>highs[-2] and lows[-1]>lows[-2]: trend='Bullish (higher swing highs and lows)'
        elif highs[-1]<highs[-2] and lows[-1]<lows[-2]: trend='Bearish (lower swing highs and lows)'
    prior=bars[-21:-1]; last=bars[-1]
    support=min(b['l'] for b in prior); resistance=max(b['h'] for b in prior)
    atr=sum(max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(bars[-15:-1],bars[-14:]))/14
    facts=[]
    if method=='SMC':
        if last['h']>resistance and last['c']<resistance: facts.append('Buy-side sweep: last wick exceeded the prior 20-bar high and closed back below.')
        if last['l']<support and last['c']>support: facts.append('Sell-side sweep: last wick exceeded the prior 20-bar low and closed back above.')
        if highs and bars[-2]['c']<=highs[-1]<last['c']: facts.append('Close broke the latest confirmed swing high (bullish structure break).')
        if lows and bars[-2]['c']>=lows[-1]>last['c']: facts.append('Close broke the latest confirmed swing low (bearish structure break).')
        a,_,c=bars[-3:]
        if c['l']>a['h']: facts.append(f'Latest 3-bar bullish FVG: {a["h"]:.5f}–{c["l"]:.5f}.')
        if c['h']<a['l']: facts.append(f'Latest 3-bar bearish FVG: {c["h"]:.5f}–{a["l"]:.5f}.')
        if not facts: facts=['No fresh sweep, swing break, or 3-bar FVG confirmed on the latest closed candle.']
    else:
        prev=bars[-2]
        if last['c']>last['o'] and prev['c']<prev['o'] and last['o']<=prev['c'] and last['c']>=prev['o']: facts.append('Bullish body engulfing on the latest closed candle.')
        if last['c']<last['o'] and prev['c']>prev['o'] and last['o']>=prev['c'] and last['c']<=prev['o']: facts.append('Bearish body engulfing on the latest closed candle.')
        facts.append('Close above prior range.' if last['c']>resistance else 'Close below prior range.' if last['c']<support else 'Close inside prior 20-bar range.')
    return dict(trend=trend,support=support,resistance=resistance,atr=atr,facts=facts)

def report(bars,symbol,tf,method):
    a=analyze(bars,method); s=a['support']; r=a['resistance']
    stamp=datetime.fromtimestamp(bars[-1]['t']+TIMEFRAMES[tf][1],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return (f'SR MARKET VIEW | {symbol} | {tf} | {method}\n\nTrend: {a["trend"]}\nLast closed price: {bars[-1]["c"]:.5f}\nKey levels (prior 20 bars):\nSupport: {s:.5f} | Resistance: {r:.5f}\n\n'
        +'\n'.join(a['facts'])
        +f'\n\nBullish scenario: closed-candle break above {r:.5f}, then a retest holding above it. Invalidated by a close back below {r:.5f}.\n'
        +f'Bearish scenario: closed-candle break below {s:.5f}, then a retest holding below it. Invalidated by a close back above {s:.5f}.\n'
        +f'\nData: Twelve Data | last candle closed {stamp}\nRetrieved: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}\n'
        +'Closed candles only. Rule-based educational scenarios, not trade signals or financial advice. Provider prices may differ from MT5. Trading involves risk.')

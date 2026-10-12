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



def ema_series(values, period):
    """SMA seed, then exponential smoothing; missing warmup remains None."""
    result=[None]*len(values)
    if len(values)<period:return result
    value=sum(values[:period])/period;result[period-1]=value
    alpha=2/(period+1)
    for i in range(period,len(values)):
        value=alpha*values[i]+(1-alpha)*value;result[i]=value
    return result


def indicators(bars):
    if len(bars)<60:raise DataError('At least 60 closed candles needed. No analysis generated.')
    closes=[b['c'] for b in bars]
    changes=[b-a for a,b in zip(closes,closes[1:])]
    gain=sum(max(x,0) for x in changes[:14])/14
    loss=sum(max(-x,0) for x in changes[:14])/14
    for x in changes[14:]:
        gain=(gain*13+max(x,0))/14;loss=(loss*13+max(-x,0))/14
    rsi=50 if gain==loss==0 else 100 if loss==0 else 100-100/(1+gain/loss)
    fast=ema_series(closes,12);slow=ema_series(closes,26)
    macd=[f-s for f,s in zip(fast,slow) if f is not None and s is not None]
    signal=ema_series(macd,9)
    histogram=macd[-1]-signal[-1];previous=macd[-2]-signal[-2]
    cross='bullish crossover on last close' if previous<=0<histogram else 'bearish crossover on last close' if previous>=0>histogram else 'no new crossover'
    tr=[max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(bars,bars[1:])]
    atr=sum(tr[:14])/14
    for x in tr[14:]:atr=(13*atr+x)/14
    return dict(ema20=ema_series(closes,20)[-1],ema50=ema_series(closes,50)[-1],ema200=ema_series(closes,200)[-1],rsi=rsi,macd=macd[-1],signal=signal[-1],histogram=histogram,cross=cross,atr=atr)


def reward_risk(entry,stop,target):
    risk=abs(entry-stop)
    if risk<=0 or (entry-stop)*(target-entry)<=0:return None
    return abs(target-entry)/risk


def market_setup(bars,method):
    a=analyze(bars,method);i=indicators(bars);close=bars[-1]['c']
    bull=[close>i['ema20']>i['ema50'],i['rsi']>=55,i['histogram']>0,a['trend'].startswith('Bullish')]
    bear=[close<i['ema20']<i['ema50'],i['rsi']<=45,i['histogram']<0,a['trend'].startswith('Bearish')]
    direction=1 if sum(bull)>=3 else -1 if sum(bear)>=3 else 0
    score=sum(bull) if direction==1 else sum(bear) if direction==-1 else max(sum(bull),sum(bear))
    # A deliberately conditional breakout/retest template, not a market order.
    # Targets are explicit R-multiple projections, never labelled observed resistance.
    setup=None
    if direction and i['atr']>0:
        level=a['resistance'] if direction==1 else a['support']
        entry=level
        stop=(a['support']-0.1*i['atr']) if direction==1 else (a['resistance']+0.1*i['atr'])
        risk=abs(entry-stop)
        targets=[entry+direction*risk*n for n in (1,2,3)]
        if entry>0 and stop>0 and all(t>0 for t in targets) and risk>0:
            setup=dict(direction=direction,entry=entry,stop=stop,targets=targets,
                       broken=close>level if direction==1 else close<level)
    return a,i,direction,score,setup


def detailed_report(bars,symbol,tf,method):
    a,i,direction,score,setup=market_setup(bars,method)
    digits=3 if 'JPY' in symbol else 5 if symbol not in ('XAUUSD','XAGUSD','BTCUSD','ETHUSD') else 2 if symbol!='XAGUSD' else 3
    def p(value):return f'{value:,.{digits}f}'
    close=bars[-1]['c'];support=a['support'];resistance=a['resistance']
    stamp=datetime.fromtimestamp(bars[-1]['t']+TIMEFRAMES[tf][1],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    bias='🟢 BULLISH VIEW' if direction==1 else '🔴 BEARISH VIEW' if direction==-1 else '🟡 MIXED / WAIT'
    lines=[f'📊 SR MARKET VIEW | {symbol} | {tf} | {method}',bias,
           f'Confluence: {score}/4 checks aligned — rule-based, not a win probability.',
           f'Last closed price: {p(close)}','']
    if setup:
        side='BUY' if direction==1 else 'SELL';level=p(setup['entry'])
        condition=f'{tf} close above {level}, then a retest that closes above it' if direction==1 else f'{tf} close below {level}, then a retest that closes below it'
        lines += [f'📍 CONDITIONAL {side} PLAN — not an immediate entry',
                  'Status: breakout close seen; retest confirmation still required.' if setup['broken'] else 'Status: waiting for breakout and retest confirmation.',
                  f'Trigger: {condition}.',f'Entry reference: {level} (planned retest level, not a live quote)',
                  f'🛑 SL reference: {p(setup["stop"])} | distance {abs(setup["entry"]-setup["stop"])/setup["entry"]*100:.2f}%',
                  '🎯 Projected targets (risk multiples, NOT observed price levels):']
        for n,target in enumerate(setup['targets'],1):
            lines.append(f'TP{n}: {p(target)} | R:R 1:{reward_risk(setup["entry"],setup["stop"],target):.2f}')
        lines += ['SL uses the opposite range boundary plus a 0.1 ATR buffer. Recalculate R:R for actual fill and costs.',
                  f'❌ Plan invalidation: a {tf} close back '+('below ' if direction==1 else 'above ')+level+' after the breakout. This is separate from the protective SL.','']
    else:
        lines += ['📍 NO TRADE PLAN: indicators conflict or a valid risk setup is unavailable. Entry / SL / targets withheld.','']
    trend='above' if close>i['ema50'] else 'below' if close<i['ema50'] else 'at'
    momentum='positive' if i['histogram']>0 else 'negative' if i['histogram']<0 else 'flat'
    lines += ['🧠 WHY THIS VIEW',f'Structure: {a["trend"]}. Price is {trend} EMA50; MACD histogram is {momentum}.',
              *a['facts'],
              'EMA alignment, RSI direction, MACD histogram and confirmed swing structure form the four checks.',
              '📐 INDICATORS (computed from closed candles)',
              f'EMA20 {p(i["ema20"])} | EMA50 {p(i["ema50"])} | EMA200 '+(p(i['ema200']) if i['ema200'] is not None else 'unavailable (<200 candles)'),
              f'RSI14 {i["rsi"]:.1f} | MACD(12,26,9) {i["macd"]:.5f} | signal {i["signal"]:.5f}',
              f'Histogram {i["histogram"]:.5f}: {i["cross"]}. ATR14 {p(i["atr"])}.','',
              f'📌 KEY LEVELS — prior 20 closed bars: support {p(support)} | resistance {p(resistance)}',
              f'Bullish scenario: {tf} close above {p(resistance)} and retest holds. Invalid if close returns below it.',
              f'Bearish scenario: {tf} close below {p(support)} and retest holds. Invalid if close returns above it.']
    if i['rsi']>=70 or i['rsi']<=30:lines.append('⚠️ RSI is at an extreme: chasing price is risky; this alone does not predict a reversal.')
    if i['ema200'] is not None and ((direction==1 and close<i['ema200']) or (direction==-1 and close>i['ema200'])):
        lines.append('⚠️ Short-term bias opposes EMA200; broader trend confirmation is missing.')
    lines += ['Projected targets can meet intervening support/resistance; none is guaranteed. Volume/news are not verified in this report.',
              '',f'🕐 Candle closed: {stamp}',f'Data: Twelve Data | {len(bars)} closed candles | retrieved {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
              'Educational scenario, not personalised advice. Feed may differ from MT5/TradingView. Spreads and slippage excluded.']
    return '\n'.join(lines)

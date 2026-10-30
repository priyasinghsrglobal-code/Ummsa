"""On-demand, closed-candle directional market classification; independent of both setup strategies."""
from engine import indicators
VERSION='directional-confluence-v1'
def classify(bars,symbol,tf,step):
    if len(bars)<60:raise ValueError('At least 60 closed bars required')
    i=indicators(bars);last=bars[-1];prior=bars[-2]
    atr=i['atr'];price=last['c']
    if atr<=0 or price<=0:raise ValueError('Invalid volatility')
    up=price>i['ema20']>i['ema50'];down=price<i['ema20']<i['ema50']
    buy=up and i['rsi']>=55 and i['histogram']>0 and last['c']>prior['c']
    sell=down and i['rsi']<=45 and i['histogram']<0 and last['c']<prior['c']
    side='BUY' if buy else 'SELL' if sell else 'NEUTRAL'
    reasons=[]
    reasons.append('EMA20 above EMA50 with price above both' if up else 'EMA20 below EMA50 with price below both' if down else 'EMAs/price are not aligned')
    reasons.append(f'RSI {i["rsi"]:.1f}')
    reasons.append('MACD bullish momentum' if i['histogram']>0 else 'MACD bearish momentum' if i['histogram']<0 else 'MACD flat momentum')
    reasons.append('Last closed candle rising' if last['c']>prior['c'] else 'Last closed candle falling' if last['c']<prior['c'] else 'No last-candle direction')
    entry=stop=None;targets=[]
    if side!='NEUTRAL':
        entry=price
        stop=entry-1.5*atr if side=='BUY' else entry+1.5*atr
        if stop<=0:side='NEUTRAL';entry=stop=None
        else:
            risk=abs(entry-stop);direction=1 if side=='BUY' else -1
            targets=[entry+direction*risk*r for r in (1,2,3)]
            if min(targets)<=0:side='NEUTRAL';entry=stop=None;targets=[]
    return dict(strategy=VERSION,side=side,status='Directional bias' if side!='NEUTRAL' else 'No aligned direction',
        entry=entry,stop=stop,targets=targets,atr=atr,reason=' · '.join(reasons),
        conditions={'ema_alignment':up or down,'rsi_aligned':i['rsi']>=55 if side=='BUY' else i['rsi']<=45 if side=='SELL' else False,
                    'macd_aligned':i['histogram']>0 if side=='BUY' else i['histogram']<0 if side=='SELL' else False},
        note='Indicative levels at last closed candle only. Not an executable price or confirmed strategy setup. Spread, news and slippage not modeled.')

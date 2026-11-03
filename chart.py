from io import BytesIO
from datetime import datetime, timezone
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from engine import analyze, TIMEFRAMES

def render(bars,symbol,tf,method):
    levels=analyze(bars,method); shown=bars[-60:]
    with plt.style.context('dark_background'):
        fig,ax=plt.subplots(figsize=(12,7),dpi=150)
        fig.patch.set_facecolor('#111319');ax.set_facecolor('#111319')
        for i,b in enumerate(shown):
            color='#33cfaa' if b['c']>=b['o'] else '#ee4858'
            ax.vlines(i,b['l'],b['h'],color=color,linewidth=1)
            ax.add_patch(Rectangle((i-.3,min(b['o'],b['c'])),.6,max(abs(b['c']-b['o']),b['c']*.000001),facecolor=color))
        for key,color in [('support','#33cfaa'),('resistance','#ffc857')]:
            ax.axhline(levels[key],color=color,linestyle='--',alpha=.8,label=f'{key.title()} {levels[key]:.5f}')
        ticks=list(range(0,len(shown),10));ax.set_xticks(ticks)
        ax.set_xticklabels([datetime.fromtimestamp(shown[i]['t'],timezone.utc).strftime('%d %b\n%H:%M') for i in ticks])
        ax.set_xlabel('Candle open time • UTC');ax.set_ylabel('Price (USD or quote currency)')
        ax.set_title(f'SR MARKET VIEW  |  {symbol}  •  {tf}  •  {method}',loc='left',pad=18,fontweight='bold')
        ax.grid(alpha=.1);ax.legend(loc='best');ax.ticklabel_format(axis='y',style='plain',useOffset=False)
        stamp=datetime.fromtimestamp(bars[-1]['t']+TIMEFRAMES[tf][1],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        fig.text(.08,.025,f'Twelve Data • Last candle closed {stamp} • Educational use only',fontsize=9,color='#afb3bd')
        fig.tight_layout(rect=(0,.05,1,1));out=BytesIO();fig.savefig(out,format='png');plt.close(fig)
        return out.getvalue()

def signal_render(bars,symbol,tf,signal):
    """Minimal, realistic candlestick chart from validated market candles.

    Generated locally: not an actual TradingView.com screenshot or independent
    tick feed. Display only real closed candles and derived indicative levels.
    """
    from engine import ema_series
    shown=bars[-65:]
    side=signal.get('side','NEUTRAL')
    closes=[b['c'] for b in bars]
    ema20=ema_series(closes,20)[-len(shown):]
    ema50=ema_series(closes,50)[-len(shown):]
    levels=[]
    if side in ('BUY','SELL') and signal.get('entry') and signal.get('stop') and signal.get('targets'):
        levels=[('ENTRY',signal['entry'],'#d2ad60'),
                ('SL',signal['stop'],'#ed7378')]
        levels.extend((f'T{i+1}',v,'#5bbd9d') for i,v in enumerate(signal['targets'][:2]))
    digits=2 if symbol in ('XAUUSD','BTCUSD','ETHUSD') else 3 if symbol in ('XAGUSD','USDJPY','GBPJPY') else 5
    with plt.style.context('dark_background'):
        fig,ax=plt.subplots(figsize=(12,7),dpi=145)
        fig.patch.set_facecolor('#111722')
        ax.set_facecolor('#111722')
        for i,b in enumerate(shown):
            color='#4bb59d' if b['c']>=b['o'] else '#db6673'
            ax.vlines(i,b['l'],b['h'],color=color,linewidth=.95,zorder=3)
            body=max(abs(b['c']-b['o']),b['c']*.000001)
            ax.add_patch(Rectangle((i-.29,min(b['o'],b['c'])),.58,body,
                                   facecolor=color,edgecolor=color,linewidth=.5,zorder=4))
        for data,color,label in ((ema20,'#f0bd64','EMA20'),(ema50,'#70a1d8','EMA50')):
            x=[i for i,v in enumerate(data) if v is not None]
            y=[v for v in data if v is not None]
            if len(x)>1:ax.plot(x,y,color=color,alpha=.92,linewidth=1.3,label=label,zorder=5)
        for name,value,color in levels:
            ax.axhline(value,color=color,alpha=.9,linestyle=(0,(5,3)),linewidth=1.15,zorder=2)
            ax.text(len(shown)+1,value,f' {name} {value:.{digits}f} ',
                    color=color,va='center',fontsize=8.5,fontweight='medium',
                    bbox=dict(boxstyle='square,pad=.25',facecolor='#111722',edgecolor=color,linewidth=.6))
        ax.set_xlim(-1,len(shown)+23)
        ax.grid(alpha=.11,color='#8290a8',linewidth=.6)
        ax.set_axisbelow(True)
        ax.yaxis.tick_right()
        ax.tick_params(axis='both',colors='#a6afbe',labelsize=9,length=0,pad=8)
        ax.spines[['left','right','top']].set_visible(False)
        ax.spines['bottom'].set_color('#344052')
        ax.set_title(f'{symbol}  ·  {tf.upper()}  ·  {side}',loc='left',
                     fontsize=17,fontweight='bold',pad=16,color='#ecf0f7')
        ax.ticklabel_format(axis='y',style='plain',useOffset=False)
        step=max(1,len(shown)//6)
        ticks=list(range(0,len(shown),step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([datetime.fromtimestamp(shown[i]['t'],timezone.utc).strftime('%d %b\n%H:%M')
                            for i in ticks])
        if len(shown)>=3:
            ax.annotate('▲' if side=='BUY' else '▼' if side=='SELL' else '',
                xy=(len(shown)-1,shown[-1]['l'] if side=='BUY' else shown[-1]['h']),
                xytext=(0,-25 if side=='BUY' else 18),textcoords='offset points',
                color='#5bbd9d' if side=='BUY' else '#ed7378',fontsize=13,
                ha='center',fontweight='bold')
        ax.legend(loc='upper left',frameon=False,fontsize=9,
                  labelcolor='#a6afbe',ncol=2,bbox_to_anchor=(.35,1.07))
        stamp=datetime.fromtimestamp(bars[-1]['t']+TIMEFRAMES[tf][1],
                                    timezone.utc).strftime('%d %b %Y  %H:%M UTC')
        fig.text(.08,.026,f'Closed candle  {stamp}  |  Indicative levels · No broker execution',
                 color='#9faabd',fontsize=8.5)
        fig.tight_layout(rect=(0,.05,1,.99))
        out=BytesIO()
        fig.savefig(out,format='png',facecolor=fig.get_facecolor())
        plt.close(fig)
        return out.getvalue()

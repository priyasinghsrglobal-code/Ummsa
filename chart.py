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

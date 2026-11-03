"""Conservative four-slot Telegram channel signal publisher.

Dubai local time slots: 10:00, 13:00, 16:00, 19:00.
No NEUTRAL messages, backfilled alerts, forced trades, or five-minute
reminder spam.  A signal is sent only when verified *closed-candle*
data meets an existing deterministic strategy on that particular slot.
A failed market-data request never creates a signal.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import time

import features
import telegram_signals as signals
from engine import TIMEFRAMES

LOG=logging.getLogger("sr")
DUBAI=ZoneInfo("Asia/Dubai")
SLOTS=(10,13,16,19)
MARKETS=("XAUUSD","XAGUSD","BTCUSD")
TIMEFRAME="15m"
WINDOW_SECONDS=9*60

def setup_db(db):
    db.executescript("""CREATE TABLE IF NOT EXISTS channel_signal_slots(
      group_id INTEGER NOT NULL,
      day TEXT NOT NULL,
      slot INTEGER NOT NULL,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      candle_close REAL NOT NULL,
      delivered REAL NOT NULL,
      PRIMARY KEY(group_id,day,slot)
    );""")

def slot_at(now):
    local=datetime.fromtimestamp(now,DUBAI)
    for hour in SLOTS:
        base=local.replace(hour=hour,minute=0,second=0,microsecond=0)
        lag=now-base.timestamp()
        if 0 <= lag <= WINDOW_SECONDS:
            return (local.date().isoformat(),hour,base.timestamp())
    return None

def valid_signal(result):
    if result.get("side") not in ("BUY","SELL"):return False
    levels=result.get("targets") or []
    entry=result.get("entry")
    stop=result.get("stop")
    return (isinstance(entry,(int,float)) and isinstance(stop,(int,float))
            and entry>0 and stop>0 and len(levels)>=2
            and all(isinstance(x,(int,float)) and x>0 for x in levels[:2])
            and ((result["side"]=="BUY" and stop<entry<levels[0]<levels[1])
                 or (result["side"]=="SELL" and levels[1]<levels[0]<entry<stop)))

def tick(candles_fn,publish_fn,now=None):
    """Check one Dubai slot; return sent count. Does nothing outside its slot.

    - One signal maximum per channel across ALL three symbols per slot.
    - Latest closed candle must be from this slot (not a stale quote).
    - No duplicate same-side same-symbol alert later on the same day.
    - Each channel's existing opt-in configuration is respected.
    - Telegram send errors do not mark a slot as delivered.
    """
    now=time.time() if now is None else now
    slot=slot_at(now)
    if not slot:return 0
    day,hour,close_target=slot
    with features.connect() as db:
        signals.setup_db(db)
        setup_db(db)
        members=signals.enabled_group_markets(db,tf=TIMEFRAME,strategy="direction")
        by_group={}
        for sub in members:
            if sub["symbol"] in MARKETS:
                by_group.setdefault(sub["group_id"],set()).add(sub["symbol"])
        eligible={}
        for group,markets in by_group.items():
            if db.execute("SELECT 1 FROM channel_signal_slots WHERE group_id=? AND day=? AND slot=?",
                          (group,day,hour)).fetchone():
                continue
            eligible[group]=markets
    if not eligible:return 0
    # The rotation avoids giving Gold priority at every one of the four slots.
    start=(hour//3+int(day[-2:]))%len(MARKETS)
    order=MARKETS[start:]+MARKETS[:start]
    candidates={}
    for symbol in order:
        if not any(symbol in markets for markets in eligible.values()):
            continue
        try:
            bars=candles_fn(symbol,TIMEFRAME)
            candle_close=bars[-1]["t"]+TIMEFRAMES[TIMEFRAME][1]
            if not (close_target<=candle_close<=now):
                continue
            result=signals.calculate(bars,symbol,TIMEFRAME,"direction")
            if valid_signal(result):
                candidates[symbol]=(bars,result,candle_close)
        except (ValueError,TypeError,KeyError) as exc:
            LOG.warning("Channel signal data invalid for %s: %s",symbol,type(exc).__name__)
        except Exception:
            LOG.warning("Channel signal feed temporarily unavailable for %s",symbol)
    sent=0
    for group,markets in eligible.items():
        for symbol in order:
            if symbol not in markets or symbol not in candidates:continue
            bars,result,candle_close=candidates[symbol]
            side=result["side"]
            with features.connect() as db:
                setup_db(db)
                existing=db.execute(
                    "SELECT 1 FROM channel_signal_slots WHERE group_id=? AND day=? AND slot=?",
                    (group,day,hour)).fetchone()
                previous=db.execute(
                    "SELECT side FROM channel_signal_slots WHERE group_id=? AND day=? AND symbol=? ORDER BY slot DESC LIMIT 1",
                    (group,day,symbol)).fetchone()
            if existing:break
            if previous and previous["side"]==side:continue
            message=signals.format_signal(result,symbol,TIMEFRAME,"direction",candle_close)
            try:
                publish_fn(group,message,bars,result,symbol,TIMEFRAME)
            except Exception:
                LOG.warning("Channel signal publish failed; slot remains unsent")
                break
            with features.connect() as db:
                setup_db(db)
                db.execute("INSERT OR IGNORE INTO channel_signal_slots VALUES (?,?,?,?,?,?,?)",
                           (group,day,hour,symbol,side,candle_close,now))
            LOG.info("Confirmed scheduled signal posted for %s at Dubai hour %s",symbol,hour)
            sent+=1
            break
    return sent

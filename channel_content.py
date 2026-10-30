"""Opt-in channel news and daily engagement. No generated or guessed headlines.

Trusted RSS: Federal Reserve monetary policy and U.S. Bureau of Labor Statistics.
All external network operations have hard timeouts. A feed error cannot stop signal
monitoring. News and polls are independently rate-limited per channel in SQLite.
"""
import email.utils
import hashlib
import html
import logging
import os
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import features

LOG=logging.getLogger('sr')
RSS_SOURCES=(
    ('Federal Reserve','https://www.federalreserve.gov/feeds/press_monetary.xml',
     ('fomc','monetary','interest rate','federal funds','inflation','policy','federal reserve','statement','balance sheet')),
    ('U.S. Bureau of Labor Statistics','https://www.bls.gov/feed/bls_latest.rss',
     ('consumer price','cpi','inflation','payroll','employment situation','unemployment','producer price','ppi','job openings','jolts','employment cost')),
)
APPROVED_DOMAINS={'www.federalreserve.gov','federalreserve.gov','www.bls.gov','bls.gov'}
NEWS_AGE_SECONDS=48*3600
NEWS_INTERVAL_SECONDS=12*3600
POLL_QUESTIONS=[
    ('Gold trade se pehle tum sabse pehle kya check karte ho?',
     ['Market structure','Liquidity','News calendar','Risk/SL']),
    ('Ek profitable setup miss ho gaya. Next move?',
     ['Wait for next setup','Chase the entry','Increase lot','Close charts']),
    ('Trading mein sabse hard kya lagta hai?',
     ['Patience','Risk management','Discipline','Overtrading']),
    ('Gold ke liye tumhara preferred session?',
     ['London','New York','London–NY overlap','No fixed session']),
    ('Back-to-back 2 losses ke baad?',
     ['Stop for the day','Reduce risk','Continue same plan','Increase size']),
    ('Chart pe tum kis cheez ko zyada weight dete ho?',
     ['Structure','Support / Resistance','Liquidity sweep','Momentum']),
    ('Trade miss ho jaaye toh?',
     ['No trade is also a trade','Late entry','Look for reversal','Switch pair']),
    ('Journal maintain karte ho?',
     ['Every trade','Only losses','Sometimes','Not yet']),
    ('Risk per trade kya follow karte ho?',
     ['Below 0.5%','0.5–1%','1–2%','Varies by setup']),
    ('Market unclear ho toh?',
     ['Stay out','Lower timeframe','Trade smaller','Wait for session']),
]
DUBAI=timedelta(hours=4)

def setup_db(db):
    db.executescript("""CREATE TABLE IF NOT EXISTS channel_content(
        channel_id INTEGER PRIMARY KEY,
        news_enabled INTEGER NOT NULL DEFAULT 1,
        engagement_enabled INTEGER NOT NULL DEFAULT 1,
        last_news_at REAL NOT NULL DEFAULT 0,
        last_engage_day TEXT NOT NULL DEFAULT '',
        activated_at REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_content_posts(
        channel_id INTEGER NOT NULL,
        story_id TEXT NOT NULL,
        posted_at REAL NOT NULL,
        PRIMARY KEY(channel_id,story_id));""")

def set_settings(channel,news=None,engagement=None):
    with features.connect() as db:
        setup_db(db)
        db.execute('INSERT OR IGNORE INTO channel_content(channel_id,activated_at) VALUES (?,?)',
                   (channel,time.time()))
        if news is not None:
            db.execute('UPDATE channel_content SET news_enabled=? WHERE channel_id=?',(int(bool(news)),channel))
        if engagement is not None:
            db.execute('UPDATE channel_content SET engagement_enabled=? WHERE channel_id=?',(int(bool(engagement)),channel))

def settings(channel):
    with features.connect() as db:
        setup_db(db)
        row=db.execute('SELECT * FROM channel_content WHERE channel_id=?',(channel,)).fetchone()
        return dict(row) if row else None

def bootstrap_active_channels(telegram,signals):
    """Discover existing opted-in signal channels; never enable groups."""
    with features.connect() as db:
        setup_db(db)
        signals.setup_db(db)
        ids=[r['group_id'] for r in db.execute(
            'SELECT group_id FROM signal_groups WHERE enabled=1 AND group_id NOT IN '
            '(SELECT channel_id FROM channel_content)')]
    if not ids:
        return
    me=telegram('getMe')
    for channel in ids:
        try:
            info=telegram('getChat',chat_id=channel)
            if info.get('type')!='channel':
                continue
            member=telegram('getChatMember',chat_id=channel,user_id=me['id'])
            if member.get('status') not in ('administrator','creator') or member.get('can_post_messages') is False:
                continue
            set_settings(channel,news=True,engagement=True)
            LOG.info('Existing active channel subscribed to news and engagement')
        except Exception:
            LOG.warning('Channel content activation pending admin access')

def _clean(text):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>','',text or ''))).strip()[:210]

def fetch_official_news(now=None):
    """Only titles and timestamps published on approved official feeds."""
    now=now or time.time()
    items=[]
    for source,url,keywords in RSS_SOURCES:
        try:
            request=urllib.request.Request(url,headers={
                'User-Agent':'Mozilla/5.0 (compatible; MarketNewsReader/1.0)',
                'Accept':'application/rss+xml,application/xml,text/xml'})
            with urllib.request.urlopen(request,timeout=7) as response:
                blob=response.read(650001)
            if len(blob)>650000:
                raise ValueError('oversized feed')
            root=ET.fromstring(blob)
            for el in root.findall('.//item')[:45]:
                title=_clean(el.findtext('title'))
                link=(el.findtext('link') or '').strip()
                published=el.findtext('pubDate') or ''
                if not title or not link or not published:continue
                if urllib.parse.urlsplit(link).scheme!='https':continue
                if urllib.parse.urlsplit(link).hostname not in APPROVED_DOMAINS:continue
                if not any(word in title.lower() for word in keywords):continue
                try:
                    dt=email.utils.parsedate_to_datetime(published)
                    if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
                    ts=dt.timestamp()
                except (TypeError,ValueError,OverflowError):continue
                if ts>now+300 or now-ts>NEWS_AGE_SECONDS:continue
                uid=hashlib.sha256(link.encode()).hexdigest()[:28]
                items.append(dict(id=uid,title=title,link=link,source=source,published=ts))
        except Exception:
            LOG.warning('Official news feed unavailable: %s',source)
    return sorted(items,key=lambda item:item['published'],reverse=True)

def format_news(item):
    when=datetime.fromtimestamp(item['published'],timezone.utc).strftime('%d %b %Y, %H:%M UTC')
    impact=('Fed policy updates may affect USD, yields and gold volatility; direction is not guaranteed.'
            if item['source']=='Federal Reserve'
            else 'U.S. inflation and jobs data can move USD and gold; check the actual release before trading.')
    return (f'📰 MARKET NEWS | USD & GOLD\n'
            f'{item["title"]}\n\n'
            f'Why it matters: {impact}\n'
            f'Source: {item["source"]} · {when}\n'
            f'{item["link"]}')

def _today_dubai(now):
    return datetime.fromtimestamp(now,timezone(DUBAI)).strftime('%Y-%m-%d')

def tick(telegram,signals,now=None):
    """Run from the bot's single polling thread at most once per 30 minutes."""
    now=now or time.time()
    bootstrap_active_channels(telegram,signals)
    with features.connect() as db:
        setup_db(db)
        signals.setup_db(db)
        rows=[dict(x) for x in db.execute(
            'SELECT c.* FROM channel_content c JOIN signal_groups s '
            'ON s.group_id=c.channel_id WHERE s.enabled=1 AND '
            '(c.news_enabled=1 OR c.engagement_enabled=1)')]
    if not rows:return
    today=_today_dubai(now)
    news_candidates=fetch_official_news(now) if any(x['news_enabled'] and now-x['last_news_at']>=NEWS_INTERVAL_SECONDS for x in rows) else []
    for row in rows:
        channel=row['channel_id']
        if row['news_enabled'] and now-row['last_news_at']>=NEWS_INTERVAL_SECONDS:
            for item in news_candidates:
                with features.connect() as db:
                    setup_db(db)
                    old=db.execute('SELECT 1 FROM channel_content_posts WHERE channel_id=? AND story_id=?',
                                   (channel,item['id'])).fetchone()
                if old:continue
                try:
                    telegram('sendMessage',chat_id=channel,text=format_news(item),
                             disable_web_page_preview=True)
                    with features.connect() as db:
                        setup_db(db)
                        db.execute('INSERT OR IGNORE INTO channel_content_posts VALUES (?,?,?)',
                                   (channel,item['id'],now))
                        db.execute('UPDATE channel_content SET last_news_at=? WHERE channel_id=?',
                                   (now,channel))
                    LOG.info('Verified official news delivered to channel')
                except Exception:
                    LOG.warning('Could not deliver verified news to subscribed channel')
                break
        # One short original poll daily; no generated facts or invented market moves.
        if row['engagement_enabled'] and row['last_engage_day']!=today:
            question,options=POLL_QUESTIONS[datetime.fromisoformat(today).date().toordinal()%len(POLL_QUESTIONS)]
            try:
                telegram('sendPoll',chat_id=channel,question=question,options=options,
                         is_anonymous=True,type='regular')
                with features.connect() as db:
                    setup_db(db)
                    db.execute('UPDATE channel_content SET last_engage_day=? WHERE channel_id=?',(today,channel))
                LOG.info('Engagement poll delivered to channel')
            except Exception:
                LOG.warning('Channel engagement poll failed (check posting permission)')
    # Keep a bounded history of posted articles.
    with features.connect() as db:
        db.execute('DELETE FROM channel_content_posts WHERE posted_at<?',(now-45*86400,))

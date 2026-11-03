# SR Market View assistant

Private-chat Telegram assistant with request-only TradingView snapshots and validated Twelve Data closed-candle analysis. Never executes trades or accesses CRM accounts.

## Features
- Respectful greeting, short contextual AI answers, persistent recent context and preferences.
- Verified, dated SR FAQ summaries and deterministic support/IB guidance even without AI quota. Website account figures conflict; do not infer missing terms.
- `/menu` for deterministic analysis selection; `/help` lists commands.
- `/preferences language english|hinglish|hindi`, `/preferences symbol XAUUSD`, `/preferences timeframe 15m`, `/preferences style intraday`.
- `/watchlist XAUUSD EURUSD` stores favourites; no automatic price notifications.
- `/review buy ENTRY SL TP` or natural text with `buy entry ... sl ... tp ...` checks supplied price geometry and reward:risk, not market validity.
- `/risk BALANCE RISK_PERCENT SL_TICKS TICK_VALUE_PER_LOT LOT_STEP` calculates downward-rounded volume from user-supplied broker specs in account currency. Costs, slippage, min/max lots must be checked separately.
- `/journal SYMBOL buy|sell ENTRY SL TP RESULT_R reason` saves user-reported completed trades. `/weekly` reviews trailing seven days, with note-tag counts; no MT5 sync or scheduled report.
- `/learn 1` to `/learn 4`: price action, SMC, risk, psychology.
- `/remind MINUTES message`, `/reminders`, `/cancel_reminder ID`: opt-in one-time text reminders; polling cadence may delay delivery. No live price/news alerts. Reminders are retried after temporary Telegram failure and may duplicate if delivery succeeds but persistence fails.
- Feedback buttons store ratings; `/privacy`, `/reset`, `/delete_my_data confirm` expose storage controls. Recent conversation is sent to the configured AI provider. Failed-question snippets retained 7 days for admin review; event counts and feedback retained 30 days. Journals persist until deletion.

## Owner setup
1. In the bot send `/myid`. It works even when access is denied.
2. Set Railway `ADMIN_USER_IDS` to comma-separated numeric owner IDs. No automatic first-user admin.
3. Owner sends `/approve ID` for each selected user, then `/private on`. `/remove ID` revokes; `/private off` reopens. Owners cannot lock themselves out. All callback queries authorize the sender before accessing state.
4. `/admin` shows 24h API request counts (not provider billing/tokens), aggregate ratings, and controls. `/unanswered` shows recent failed question snippets. `/faq_set TOPIC | English | Hinglish` updates a supported topic; `/faq_delete TOPIC` restores source summary. FAQ entries expire after 30 days and require review.

## Deployment
Existing keys: `TELEGRAM_BOT_TOKEN`, `TWELVE_DATA_API_KEY`, `GEMINI_API_KEY` (or `Gemini API Key`), `CHART_IMG_API_KEY` (or `chart-img API`). Never commit secrets.
Mount persistent Railway volume at `/data`; set `BOT_DB_PATH=/data/bot.sqlite3`. One replica only (Telegram polling and SQLite). Docker entrypoint ensures the bot user can write to the mount and drops privileges. Railpack runs `python bot.py` and uses the same database path.
`ADMIN_USER_IDS` is deliberately unset until owner identity is supplied. Default public mode preserves current access; user explicitly activates private mode after approving IDs. `STARTUP_PROBE=true` enables optional external provider checks; normal startup does not consume chart/AI quota.
Health endpoint checks polling freshness; healthy does not imply all external providers have remaining quota.

## Tests
`BOT_DB_PATH=/tmp/sr-test.sqlite3 python -m unittest -v`
Tests mock external APIs; production provider and Telegram delivery checks are separate. Verified source pages: https://srglobalmarkets.com/ and https://srglobalmarkets.com/accounts/ (2026-10-06).


## SR Global channel publishing rules (November 2026)

Production channel publishing has a **maximum of four signal opportunities per
Dubai calendar day**: 10:00, 13:00, 16:00 and 19:00 Asia/Dubai time.

- One signal maximum **across XAUUSD, XAGUSD and BTCUSD combined** per slot.
- Publisher validates the **fresh closed M15 candle** and existing deterministic
  indicator alignment; no arbitrary or retroactively generated trade is posted.
- If a candle/data source is missing, stale, NEUTRAL or a previous same-side
  channel signal would repeat, **nothing is posted**.
- There are no forced daily minimums, no NEUTRAL/periodic market views in
  channels, no public reminder-countdown spam and no channel signals after 19:00.
- Channel signal media is a **single local chart PNG plus concise caption**.
  It contains real validated provider candles, EMA20/EMA50, Entry, SL, T1/T2.
  This is TradingView-*style*, **not** an official TradingView Advanced Chart
  screenshot or a separate tick-price source.
- Legacy private opt-in notifications remain independent.
- Channel scheduler uses SQLite deduplication persisted on the existing
  /data volume. Old user and channel databases are left intact.
- Channel publisher runs only if the existing Telegram poller is online.

Engagement is deliberately sparse: on different weekdays, one short
conversation question, Telegram poll or original text meme, optionally rewritten
by Gemini for natural tone. Verified Federal Reserve and BLS RSS headlines are
posted on an actual relevant story, at most once per channel/day.
No fabricated news headlines or live-price claims.

**Optional controls:** Set AI_ENGAGEMENT_ENABLED=false to disable paid AI
rewrites entirely; curated original text is still posted. GEMINI_API_KEY or
existing Gemini API Key supplies the key. The existing Telegram bot token,
Twelve Data key, chart API key, and database must never appear in source code.

**Market data:** Local plotting does not license underlying market feed for
public redistribution. Obtain provider permission for any public commercial
signal/chart service. A personal Twelve Data plan may be insufficient.
Provider HTTP 429/404 failures must be resolved separately and never produce
a synthetic signal.

**Prerequisites to go live:** Railway service must be on an active plan.
A custom trading strategy requires its exact measurable entry, confirmation,
risk, stop, target and invalidation rules; the currently deployed source uses
the baseline EMA20/EMA50 + RSI + MACD directional model until replaced and
backtested. Real-world channel delivery still requires an end-to-end smoke test.

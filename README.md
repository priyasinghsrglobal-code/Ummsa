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

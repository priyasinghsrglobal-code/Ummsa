# Ummsa web app — private beta

This service is separate from `python bot.py`. The existing bot files and its deployment are unchanged. Payments are deliberately deferred. New registrations remain pending until the owner approves them. Do not market this build as a validated profitable strategy.

## Deploy

Use `Dockerfile.web` and `railway.web.json`, one replica, one Gunicorn worker with threads. Mount a separate persistent volume at `/data`. Set `UMMSA_DB=/data/ummsa.sqlite3`, `OWNER_SETUP_TOKEN` to a long random secret, `TWELVE_DATA_API_KEY`, and optionally `GEMINI_API_KEY`, `GEMINI_MODEL`, `VAPID_SUBJECT=mailto:<owner email>`. Secure cookies are enabled by default. For local HTTP testing only set `COOKIE_SECURE=false`.

Open the website and use Sign in → Activate workspace. Enter the one-time owner activation code and choose an email/password. After the owner exists, bootstrap is disabled by a database check. Remove the activation variable after setup. Passwords are hashed; opaque sessions are stored hashed and expire in 24 hours. Email verification and self-service password reset are not yet integrated; owner-approved beta only.

In Admin, enable scanning. Default is OFF to avoid silently consuming provider quota. The scanner checks one selected instrument every 90 seconds (1–4 instruments); default timeframe 15m. The existing Telegram worker shares the provider key, so this service limits itself to two data requests per minute by default. Confirm total provider allowance and commercial redistribution rights before launch. Do not expose raw provider credentials to clients.

## Strategy

`breakout-retest-v1` checks the latest closed candle against the preceding 20-bar high/low, EMA20/50 alignment and RSI ≥55 / ≤45. ATR/close above 3% blocks new setups. Freeze the level and stop when a breakout closes. A later candle must touch within 0.15 ATR and close on the valid side with a directional body. Close back across the frozen level invalidates. A setup expires after eight subsequent bars. Entry reference is the confirmation candle close, not a promise of a fill. Stop is the opposite range boundary plus 0.1 ATR; targets project 1R, 2R, 3R. Confirmed monitoring expires after 32 bars. Same-candle target/stop is marked ambiguous, not counted as a win. No performance statistics are fabricated.

All calculations are chronological. No order execution exists. No spread, slippage or news blackout feed is integrated yet. Historical candle validation comes from the existing engine; instrument-specific holiday schedules need further work. A provider gap blocks new processing. Targets describe observed prices, not broker executions.

## APIs

Same-origin session authentication protects live APIs. `/api/status` and `/api/demo` are public and contain no live market results. `/api/analysis?symbol=XAUUSD&tf=15m` returns validated data, computed context and setup state. `/api/explain` accepts symbol/tf; it fetches facts on the server, never trusts client market facts. AI failure returns deterministic explanation. `/api/history`, `/api/notifications`, `/api/preferences`, `/api/push/key`, `/api/push/subscribe` support the UI. `/api/admin/*` requires an active admin. Writes require JSON and reject foreign Origin headers. CORS is not enabled. A Base44 frontend needs a separately implemented authenticated bridge; it is not already connected.

## Notifications

In-app notifications and Web Push are scoped to approved users, enabled alert preferences and watchlists. Persistent event IDs deduplicate scan repeats. Push delivery has a durable retry queue, at most five attempts and exponential delay. Browser notification tags collapse repeated deliveries. Exact-once external delivery cannot be guaranteed if a process stops after a successful send but before recording it. Push private keys live only in the persistent server database. No push messages are sent until users opt in. Telegram/Discord web-app delivery is not connected; the original Telegram bot continues unchanged.

## Charts and data separation

Demo fixtures use a separate synthetic chart and never enter production tables. Live view embeds TradingView's official widget (with provider attribution). It has its own data feed; signal calculations use Twelve Data. Custom SL/TP drawings are not supported by this widget; levels are displayed beside it. The existing Telegram chart-img integration is untouched. Indices are not offered until real provider symbol mapping/entitlements are configured.

## Checks

`python -m pip install -r web_requirements.txt`
`python -m unittest test_webapp -v`
`node --check web/app.js`

Before paid launch: verify commercial data rights, payment/access integration, email ownership/password reset, provider quotas, broker tick specs, historical/out-of-sample validation, notification device coverage, monitored backups and restore. SQLite on one persistent replica is deliberate for private beta; multi-worker/multi-replica scaling requires PostgreSQL and a coordinated queue/limiter.

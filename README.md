# Interac Intelligence Bot

Telegram bot for monitoring public sentiment and product signals related to Interac (e-Transfer, debit, competitors, customer complaints), with optional scheduled email digests and alerts.

## Project goal

This project is designed to give product/ops stakeholders a lightweight "market pulse":

- Pull fresh public mentions from web/news/social-style sources.
- Summarize sentiment and key findings with Kimi.
- Push updates automatically to subscribed Telegram chats.
- Send email digests/alerts when configured.
- Run historical deep dives across multiple time windows (`/deepscan`) for trend discovery.

## Current architecture

- Runtime: Python 3.12 (`app.py`)
- Chat interface: Telegram (`python-telegram-bot`)
- Source scraping: Selenium + headless Chromium (public pages only)
- LLM analysis: Moonshot Kimi API (`KIMI_API_KEY`)
- Optional email delivery: SMTP or Resend
- Deployment target: Railway (or any container host)

## Repository map

- `app.py` - main application, handlers, schedulers, search, analysis, email rendering/sending.
- `prompts.json` - runtime config for queries, thresholds, lookback, and prompt file paths.
- `prompts/analysis_prompt.md` - daily scan analysis prompt.
- `prompts/followup_prompt.md` - follow-up Q&A prompt (chat replies to plain text messages).
- `prompts/historical_prompt.md` - historical deep scan prompt.
- `requirements.txt` - Python dependencies.
- `Dockerfile` / `Procfile` - deployment entrypoints.
- `manifest.json` - legacy Teams manifest artifact (not used by current Telegram runtime).

## Commands

User commands:

- `/start` or `/help` - command overview and auto-subscribe current chat.
- `/subscribe` - subscribe this chat to scheduled broadcasts.
- `/unsubscribe` - stop scheduled broadcasts for this chat.
- `/status` - runtime/schedule/status snapshot.
- `/scan` - run immediate biweekly-style scan (polling) or background job (webhook).
- `/raw` - show raw mention payload from last scan.
- `/prompt` - show query/source config summary.
- any plain text message - follow-up question over latest report context.

Admin-only commands (`ADMIN_IDS`):

- `/email` - run fresh scan and send email (see timeouts below; webhook runs in background).
- `/deepscan` - run historical scan + analysis + email (with diagnostics).
- `/smtpcheck` - validate current email provider config/connectivity.
- `/fetchdiag` - quick Reddit JSON + Selenium news + DDG probe (no full scan).

## Scheduled behavior

Defined in `app.py` job queue:

- Daily sentiment scans at 6am, 10am, 2pm, 6pm EST.
- Weekly email digest at `EMAIL_WEEKLY_DAY` + `EMAIL_WEEKLY_HOUR` (EST).
  - Implemented via daily trigger + in-function guard for compatibility.

## Configuration

### Required environment variables

```bash
TELEGRAM_TOKEN=<telegram bot token>
KIMI_API_KEY=<moonshot kimi api key>
```

### Common optional environment variables

```bash
KIMI_API_URL=https://api.moonshot.ai/v1/chat/completions
KIMI_MODEL=kimi-k2.5-preview
PORT=3978
WEBHOOK_URL=
ADMIN_IDS=123456789,987654321
DAILY_LIMIT=5
```

### Email configuration (optional)

Core toggles:

```bash
EMAIL_ENABLED=1
EMAIL_PROVIDER=smtp            # smtp | resend
EMAIL_SEND_MODE=alert          # alert | weekly | always | weekly,alert
EMAIL_ALERT_DEDUP=1
EMAIL_COOLDOWN_MINUTES=0
EMAIL_WEEKLY_DAY=monday
EMAIL_WEEKLY_HOUR=9
ALERT_HIGH_THRESHOLD=85
EMAIL_FROM=you@example.com
EMAIL_TO=you@example.com,team@example.com
EMAIL_SUBJECT_PREFIX=Interac Intelligence
```

SMTP provider:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<app-password>
```

Resend provider:

```bash
RESEND_API_KEY=re_xxxxxxxxx
RESEND_API_URL=https://api.resend.com/emails
```

Notes:

- Gmail requires an App Password (not normal account password).
- Port `587` uses STARTTLS, port `465` uses implicit SSL.
- Email dedup/cooldown state is in-memory (resets on restart/redeploy).

## Selenium scraping notes

- The bot now performs source-first scraping with Selenium for Reddit, forums, X, and news pages.
- X/Twitter is best-effort only in public mode and may return partial data when anti-bot defenses block rendering.
- Railway/container runtime must include both Chromium and chromedriver binaries.
- Time and load controls:
  - `SCRAPE_TIMEOUT_SECONDS` (default `20`)
  - `SCRAPE_MAX_PAGES_PER_SOURCE` (default `4`)
  - `SCRAPE_MAX_RESULTS_PER_QUERY` (default `5`)
  - `CHROMIUM_BINARY` (default `/usr/bin/chromium`)
  - `CHROMEDRIVER_PATH` (default `/usr/bin/chromedriver`)
- Scan timeouts (`/scan`, `/email`, scheduled job): `BIWEEKLY_FETCH_TIMEOUT` (default `900` s), `BIWEEKLY_ANALYZE_TIMEOUT` (default `120` s). Selenium bundles run concurrently up to `SCRAPE_MAX_CONCURRENT_BROWSERS`; raise `BIWEEKLY_FETCH_TIMEOUT` only if scans still exceed 15 minutes.
- `SCRAPE_MAX_CONCURRENT_BROWSERS` (default `2`) — global cap on concurrent Selenium sessions (avoids OOM on small Railway plans).
- `FETCH_FALLBACK_DDG` (default `1`) — if Reddit + Selenium still yield **zero** mentions, run DuckDuckGo (`ddgs`) text/news/X supplement like the legacy path.
- **One Chromium session per query** (forums → X → news → optional Reddit Selenium in sequence) instead of three separate browsers per query.
- **Empty scan diagnostics**: when nothing is collected, the bot returns a `=== FETCH DIAGNOSTICS ===` block (Reddit task errors, Selenium row counts, whether DDG fallback ran) before the “No mentions found…” line. Use `/fetchdiag` on the host to isolate failures.

### Webhook mode (`WEBHOOK_URL` set)

- Railway and Telegram expect the webhook HTTP handler to finish quickly. `/scan` and `/email` **enqueue work in the background** and reply immediately; results arrive as follow-up messages in the same chat.

## Prompt + query configuration

Prompt text lives in markdown files:

- `prompts/analysis_prompt.md`
- `prompts/followup_prompt.md`
- `prompts/historical_prompt.md`

Operational config lives in `prompts.json`:

- `data_queries` - daily scan query groups
- `historical_queries` - `/deepscan` query groups/time windows
- `sources` - source toggles (`reddit`, `news`, `forums`, `twitter`)
- `alert_threshold` - low sentiment threshold
- `lookback_hours` - daily scan recency
- result caps (`max_mentions_per_source`, `historical_max_mentions_per_source`)

## Daily vs historical reports

- Daily scan (`/scan` + scheduled job):
  - fast monitoring view
  - sentiment score extraction (`SENTIMENT SCORE`)
  - alert/spike logic tied to thresholds
- Historical scan (`/deepscan`):
  - grouped into RECENT, MEDIUM, OLDER windows
  - designed for recurring-theme and longer-horizon insight
  - includes pre-analysis search diagnostic and raw mention counts

Email rendering is mode-aware:

- Daily reports -> daily template
- Historical reports -> historical template
- If parsing markers are missing -> styled raw-report fallback

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# local selenium runtime (macOS example):
# brew install --cask chromium
# brew install chromedriver
export TELEGRAM_TOKEN=...
export KIMI_API_KEY=...
python app.py
```

By default, bot runs in polling mode. Set `WEBHOOK_URL` to use webhook mode (`/webhook` path).

## Railway deploy

1. Push repo to GitHub.
2. In Railway: New Project -> Deploy from GitHub.
3. Add env vars (at minimum `TELEGRAM_TOKEN`, `KIMI_API_KEY`).
4. Ensure service port is `3978` (default already handled by app env).
5. Deploy and verify with Telegram `/status`.

### Staging verification (after deploy)

1. `/fetchdiag` (admin) — expect Reddit HTTP 200, Selenium news ≥ 0 or a clear error, DDG ≥ 1 row typical.
2. `/scan` — non-empty mention payload or diagnostics explaining all failures.
3. Railway logs — no Chromium OOM; optional `LAST_FETCH_DIAGNOSTICS` is logged implicitly via returned diagnostic text on empty runs.

## Known limitations

- Subscription state, latest report context, rate-limit counters, and email dedup state are in-memory only.
- No persistence layer yet (Redis/Postgres recommended for production durability).
- `manifest.json` is legacy metadata; current runtime is Telegram-first.

## Troubleshooting

- `/deepscan` says search provider failed:
  - run again and inspect diagnostic line in Telegram response.
- Emails not sending:
  - run `/smtpcheck` and verify provider-specific env vars.
- No useful findings:
  - tune `prompts/historical_prompt.md` and `historical_queries` in `prompts.json`.
- Bot responds but no scheduled pushes:
  - ensure the chat used `/subscribe`.

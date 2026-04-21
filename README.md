# Interac e-Transfer Intelligence Bot

<p align="center">
  <strong><a href="https://youtu.be/AN_Qa8JEs7I">Demo video — watch on YouTube</a></strong><br/>
  <a href="https://youtu.be/AN_Qa8JEs7I">https://youtu.be/AN_Qa8JEs7I</a>
</p>

---

Telegram bot that scans Reddit, X/Twitter, RedFlagDeals, and news for public sentiment and market signals around Interac e-Transfer and competing Canadian payment products. Runs a biweekly scan and delivers a two-column HTML email digest to stakeholders.

## What it does

- Fetches public mentions via Reddit JSON API and DuckDuckGo (web, news, X/Twitter)
- Routes results into two analysis tracks: community chatter and market intelligence
- Runs two parallel Kimi AI calls to produce focused output for each track
- Renders a styled two-column HTML email and sends it via SMTP or Resend
- Also posts plain-text reports to subscribed Telegram chats

## Architecture

- Runtime: Python 3.12 (`app.py`)
- Chat interface: Telegram (`python-telegram-bot`)
- Search: DuckDuckGo (`ddgs`) + Reddit JSON API
- LLM: Moonshot Kimi API (`KIMI_API_KEY`)
- Email: SMTP or Resend
- Deployment: Railway (any container host)
- Timezone: `zoneinfo.ZoneInfo("America/Toronto")` — DST-aware

## Repository map

```
app.py                          main application (handlers, fetch, analysis, email)
prompts.json                    query lists, source toggles, prompt file paths
prompts/
  etransfer_chatter_prompt.md   left-column prompt: community chatter (Reddit/X/forums)
  market_pulse_prompt.md        right-column prompt: competitor and ecosystem news
  biweekly_prompt.md            legacy combined prompt (registered but not used in main scan)
  curation_prompt.md            curation pass prompt (written but currently bypassed)
  followup_prompt.md            Q&A follow-up prompt (plain text messages in Telegram)
  prompt_recipe.md              prompt-engineering notes and rationale
requirements.txt
Dockerfile / Procfile
```

## Commands

Public:

| Command | What it does |
|---|---|
| `/start` or `/help` | Command overview + auto-subscribe |
| `/subscribe` | Subscribe this chat to biweekly broadcasts |
| `/unsubscribe` | Stop scheduled broadcasts |
| `/status` | Runtime/schedule/config snapshot |
| `/scan` | Run biweekly scan immediately |
| `/raw` | Show raw mention payload from last scan |
| `/prompt` | Show query/source config summary |
| plain text | Follow-up question against latest report context |

Admin only (`ADMIN_IDS`):

| Command | What it does |
|---|---|
| `/email` | Run biweekly scan and send email now |
| `/smtpcheck` | Validate email provider config/connectivity |
| `/stop` | Cancel active running tasks |

## Data pipeline

```
fetch_biweekly_mentions()
  ├── Reddit JSON API (r/personalfinancecanada, r/canada, r/ontario, etc.)
  │     etransfer_queries + competitor_queries
  ├── DDG text + news + X/Twitter per query
  │     (unrestricted queries only — site: queries skip news/Twitter)
  └── Results routed by _classify_channel_and_source() into:
        === e-TRANSFER COMMUNITY ===   (Reddit/forum social posts)
        === e-TRANSFER NEWS ===        (press, news articles)
        === COMPETITOR INTELLIGENCE === (competitor mentions)

_split_mentions_sections()
  ├── community_text  → etransfer_chatter_prompt.md  → Kimi call A
  └── market_text     → market_pulse_prompt.md        → Kimi call B
       (both run in parallel via asyncio.create_task)

analyze_biweekly() assembles report:
  SCAN DATE / e-Transfer Chatter / Market Pulse / Trend vs Last Scan
```

## Email layout

1200px wide, table-based, inline CSS, webmail-safe.

| Area | Detail |
|---|---|
| Header | Navy `#0f1f47` + 4px gold `#fdb913` border, scan timestamp |
| Left column | "PAIN POINTS" eyebrow `#c4320a`, "e-Transfer Chatter" — real user quotes from Reddit/forums |
| Right column | "MARKET PULSE" eyebrow `#5925DC`, "Payments Landscape" — product news, launches, competitive updates |
| Quote card | Left border in platform color, quote text 13px, meta row: badge pill + date + source domain link |
| Platform badges | Reddit `#FF4500`, X/Twitter `#1a1a1a`, news `#1a73e8` — community sources only |

## Configuration

### Required environment variables

```bash
TELEGRAM_TOKEN=<telegram bot token>
KIMI_API_KEY=<moonshot kimi api key>
```

### Common optional

```bash
KIMI_API_URL=https://api.moonshot.ai/v1/chat/completions
KIMI_MODEL=kimi-k2.5-preview
PORT=3978
WEBHOOK_URL=
ADMIN_IDS=123456789,987654321
DAILY_LIMIT=5
```

### Email

```bash
EMAIL_ENABLED=1
EMAIL_PROVIDER=smtp            # smtp | resend
EMAIL_SEND_MODE=weekly         # alert | weekly | always | weekly,alert
EMAIL_WEEKLY_DAY=monday
EMAIL_WEEKLY_HOUR=9
EMAIL_FROM=you@example.com
EMAIL_TO=you@example.com,team@example.com
EMAIL_SUBJECT_PREFIX=Interac Intelligence
```

SMTP:
```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<app-password>   # Gmail requires App Password
```

Resend:
```bash
RESEND_API_KEY=re_xxxxxxxxx
RESEND_API_URL=https://api.resend.com/emails
```

### `prompts.json` keys

```json
{
  "sources":          { "reddit": true, "news": true, "forums": true, "twitter": true },
  "prompt_files":     { "etransfer_chatter_prompt": "...", "market_pulse_prompt": "...", ... },
  "etransfer_queries": [ ... ],   // unrestricted — DDG fires text + news + Twitter per query
  "competitor_queries": [ ... ],  // product launches, pricing, fintech Canada
  "timezone":         "US/Eastern"
}
```

Note: queries with `site:` restrictions skip DDG news and Twitter follow-ups. Keep `etransfer_queries` unrestricted for full source coverage. Reddit content is handled separately by the Reddit API step.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=...
export KIMI_API_KEY=...
python app.py
```

Set `WEBHOOK_URL` to use webhook mode instead of polling.

## Railway deploy

1. Push repo to GitHub.
2. Railway: New Project → Deploy from GitHub.
3. Add env vars (at minimum `TELEGRAM_TOKEN`, `KIMI_API_KEY`).
4. Default port is `3978`.
5. Verify with Telegram `/status`.

## Known limitations

- Subscription state, last report, and rate-limit counters are in-memory (reset on restart).
- No persistence layer (Redis/Postgres recommended for production durability).
- DDG search quality depends on upstream indexing — X/Twitter results can be sparse.
- `manifest.json` is a legacy Teams artifact and is not used.

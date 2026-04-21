# Interac Intelligence Bot — System Overview

## 1. What This Project Is

A Python Telegram bot that continuously scans public web chatter related to Interac e-Transfer and competing Canadian payment products, then uses Moonshot Kimi AI to produce a structured two-column intelligence digest. Delivered as an HTML email and Telegram message on a biweekly cadence.

The report has two tracks:
- **e-Transfer Chatter** — real people on Reddit, X/Twitter, and forums discussing personal e-Transfer experiences (fraud, holds, limits, delays, fees).
- **Market Pulse / Payments Landscape** — competitor product launches, pricing changes, fintech news, market entries, community reactions to PayPal/Wise/KOHO/Wealthsimple/Revolut/etc.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Bot framework | `python-telegram-bot` with job queue |
| HTTP client | `httpx` |
| Search | DuckDuckGo `ddgs` + Reddit JSON API |
| LLM | Moonshot Kimi API (`kimi-k2.5-preview`) |
| Email | SMTP (`smtplib`) or Resend API |
| Timezone | `zoneinfo.ZoneInfo("America/Toronto")` — DST-aware |
| Deploy | Docker / Railway |

---

## 3. Repository Structure

```
app.py                            all runtime logic
prompts.json                      query lists, source toggles, prompt file paths
prompts/
  etransfer_chatter_prompt.md     left-column Kimi system prompt
  market_pulse_prompt.md          right-column Kimi system prompt
  biweekly_prompt.md              legacy combined prompt (not used for main scan)
  curation_prompt.md              curation prompt (written, currently bypassed)
  followup_prompt.md              follow-up Q&A prompt (Telegram plain text replies)
  prompt_recipe.md                prompt-engineering notes
requirements.txt
Dockerfile / Procfile
state/                            disk-persisted state
  biweekly_memory.json            last scan date + theme labels (for Trend vs Last Scan)
  biweekly_history.xlsx           append-only scan history spreadsheet
```

---

## 4. End-to-End Flow

### Boot

1. `app.py` reads env vars (`TELEGRAM_TOKEN`, `KIMI_API_KEY`, etc.).
2. Telegram `Application` initializes with command handlers + job queue.
3. Job queue registers scheduled biweekly broadcast (daily trigger with 14-day guard).
4. Bot runs in polling mode unless `WEBHOOK_URL` is set.

### Data Collection — `fetch_biweekly_mentions()`

1. Reddit JSON API fetches: configured subreddits × (`etransfer_queries` + `competitor_queries`).
2. DDG searches per query — three follow-ups per unrestricted query:
   - DDG text search
   - DDG news search
   - DDG X/Twitter search
   - (Queries containing `site:` skip the news + Twitter follow-ups — keep `etransfer_queries` unrestricted for full coverage.)
3. Results de-duplicated, routed by `_classify_channel_and_source()` into three labelled sections:
   - `=== e-TRANSFER COMMUNITY ===` — Reddit/forum social posts
   - `=== e-TRANSFER NEWS ===` — press releases, news articles
   - `=== COMPETITOR INTELLIGENCE ===` — competitor product mentions

### Analysis — `analyze_biweekly()`

1. `_split_mentions_sections()` separates the raw text:
   - `community_text` = e-TRANSFER COMMUNITY only
   - `market_text` = e-TRANSFER NEWS + COMPETITOR INTELLIGENCE
2. Two parallel Kimi calls via `asyncio.create_task`:
   - `call_kimi(etransfer_chatter_prompt, community_text)` → left column bullets
   - `call_kimi(market_pulse_prompt, market_text)` → right column bullets
3. Report assembled:
   ```
   SCAN DATE: ...
   e-Transfer Chatter:
   [left column bullets]
   Market Pulse:
   [right column bullets]
   Trend vs Last Scan:
   - Still active: ...
   - Went quiet: ...
   - New this scan: ...
   ```
4. Themes extracted → saved to `state/biweekly_memory.json`.
5. Full report appended to `state/biweekly_history.xlsx`.

### Delivery

- **Telegram**: `send_chunked_message()` posts plain-text report to all subscribed chats.
- **Email**: `build_email_bodies()` → `_build_biweekly_html()` renders two-column HTML → `send_email()` routes to SMTP or Resend.

---

## 5. Email HTML Architecture

Table-based, inline CSS only, webmail-safe (Gmail, Apple Mail, Outlook web).

```
<body> page background #eef2f8
  └── 1200px white card (border-radius 14px)
        ├── Header row (colspan=2)
        │     Navy #0f1f47, 4px gold #fdb913 bottom border
        │     Title: "Interac e-Transfer Intelligence"
        │     Subtitle: scan timestamp
        └── Body row
              ├── Left cell (50%) — e-Transfer Chatter
              │     Eyebrow: "PAIN POINTS" #c4320a
              │     Section title: "e-Transfer Chatter"
              │     [quote cards]
              └── Right cell (50%) — Payments Landscape
                    Eyebrow: "MARKET PULSE" #5925DC
                    Section title: "Payments Landscape"
                    [quote cards]
```

Each quote card (`_render_quote_bullets()`):
- Left border 3px in platform color (Reddit `#FF4500`, X/Twitter `#1a1a1a`, news `#1a73e8`, fallback `#d1d5db`)
- Quote text 13px / line-height 1.55
- Meta row: platform badge pill + date + source domain link
- Badges only shown for community sources (Reddit, X/Twitter, RedFlagDeals) — not corporate domains

---

## 6. Bot Commands

### Public

| Command | What it does |
|---|---|
| `/start` or `/help` | Intro + command list + auto-subscribe |
| `/subscribe` | Subscribe to biweekly broadcasts |
| `/unsubscribe` | Stop broadcasts |
| `/status` | Runtime/schedule/config snapshot |
| `/scan` | Run biweekly scan immediately |
| `/raw` | Show raw mention payload from last scan |
| `/prompt` | Show query/source config summary |
| plain text | Follow-up Q&A against latest report |

### Admin only

| Command | What it does |
|---|---|
| `/email` | Run scan + send email immediately |
| `/smtpcheck` | Validate email config/connectivity |
| `/stop` | Cancel active running tasks |

---

## 7. Configuration

### `prompts.json` structure

```json
{
  "sources": { "reddit": true, "news": true, "forums": true, "twitter": true },
  "prompt_files": {
    "etransfer_chatter_prompt": "prompts/etransfer_chatter_prompt.md",
    "market_pulse_prompt": "prompts/market_pulse_prompt.md",
    "biweekly_prompt": "prompts/biweekly_prompt.md",
    "curation_prompt": "prompts/curation_prompt.md",
    "followup_prompt": "prompts/followup_prompt.md"
  },
  "etransfer_queries": [ ... ],    // unrestricted — no site: restrictions
  "competitor_queries": [ ... ],   // product launches, pricing, fintech Canada, social comparisons
  "timezone": "US/Eastern"
}
```

### Key env vars

```bash
TELEGRAM_TOKEN=...
KIMI_API_KEY=...
KIMI_API_URL=https://api.moonshot.ai/v1/chat/completions
KIMI_MODEL=kimi-k2.5-preview
EMAIL_ENABLED=1
EMAIL_PROVIDER=smtp
EMAIL_SEND_MODE=weekly
EMAIL_FROM=...
EMAIL_TO=...
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
ADMIN_IDS=...
```

---

## 8. State and Persistence

| State | Where | Notes |
|---|---|---|
| Subscribed chats | In-memory | Resets on restart |
| Last report text | In-memory | Resets on restart |
| Last raw mentions | In-memory | Resets on restart |
| Per-user follow-up counters | In-memory | Resets on restart |
| Email dedup/cooldown | In-memory | Resets on restart |
| Last scan date + themes | `state/biweekly_memory.json` | Persisted — used for Trend vs Last Scan |
| Scan history | `state/biweekly_history.xlsx` | Persisted — append-only |

---

## 9. Prompt Files

| File | Role | Used by |
|---|---|---|
| `etransfer_chatter_prompt.md` | Left-column analysis — real user chatter | `analyze_biweekly()` Kimi call A |
| `market_pulse_prompt.md` | Right-column analysis — market/competitor intel | `analyze_biweekly()` Kimi call B |
| `biweekly_prompt.md` | Legacy combined prompt | Registered but not used for main scan |
| `curation_prompt.md` | Filter social mentions for quality | Written, currently bypassed |
| `followup_prompt.md` | Q&A over latest report | `ask_followup()` |
| `prompt_recipe.md` | Engineering notes | Reference only |

---

## 10. Known Limitations

- Core runtime state is in-memory — resets on Railway redeploy (Redis/Postgres recommended for production durability).
- Single monolithic `app.py` — high coupling, harder to test in isolation.
- DDG X/Twitter coverage depends on upstream indexing — results can be sparse.
- No automated contract tests for report output format.
- `manifest.json` is a legacy Teams artifact and is unused.

---

## 11. Quick Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=...
export KIMI_API_KEY=...
python app.py
# In Telegram: /start → /status → /scan
```

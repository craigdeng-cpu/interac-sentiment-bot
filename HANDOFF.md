# Interac e-Transfer Intelligence Bot — Handoff Document

**Built by:** Umar Darsot  
**Handed off:** April 2026  
**Repo:** https://github.com/utosrad/teams-sentiment-bot

---

## What This Is

An automated intelligence system that monitors public web chatter and competitive market signals around Interac e-Transfer and the Canadian digital payments landscape. It runs on a biweekly schedule and delivers a two-column HTML email digest to the product team — no manual work required after setup.

**Left column — e-Transfer Chatter:** Real posts from Reddit, X/Twitter, and RedFlagDeals about user pain points, fraud, delays, holds, and limit frustrations.

**Right column — Payments Landscape (Market Pulse):** Competitive intelligence — PayPal launches, Wise pricing changes, Wealthsimple features, KOHO updates, Revolut Canada news, ecosystem developments.

---

## Accounts & Credentials

Everything runs under accounts created for this handoff. All credentials live as environment variables in Railway — **do not hardcode anything**.

| Service | Account | Purpose |
|---|---|---|
| **Microsoft / Outlook** | interac.bot@outlook.com | Master account, used to sign up for everything below |
| **Railway** | interac.bot@outlook.com | Cloud hosting / deployment |
| **Resend** | interac.bot@outlook.com | Email delivery (sends the digest) |
| **Moonshot Kimi** | interac.bot@outlook.com | AI analysis (LLM that writes the bullets) |
| **Telegram** | (BotFather token) | Bot interface for running scans manually |
| **twitterapi.io** | interac.bot@outlook.com | Real Twitter/X search API |
| **GitHub** | utosrad (original owner) | Source code — you have collaborator access |
| **Domain** | darsot.ca (Umar's) | Used for sending email via bot@darsot.ca |

---

## Railway Environment Variables

These must all be set in Railway → your service → Variables tab:

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `KIMI_API_KEY` | Moonshot API key (platform.moonshot.cn) |
| `KIMI_MODEL` | Model name — set to current working model (check platform.moonshot.cn for latest) |
| `EMAIL_PROVIDER` | `resend` |
| `RESEND_API_KEY` | Resend API key |
| `EMAIL_FROM` | `bot@darsot.ca` |
| `EMAIL_TO` | Comma-separated recipient emails |
| `EMAIL_SUBJECT_PREFIX` | `Interac Intelligence` |
| `TWITTERAPI_IO_KEY` | twitterapi.io API key |
| `ADMIN_IDS` | Your Telegram user ID (get it from `/status`) |
| `MAX_MENTION_AGE_DAYS` | `120` (how far back to look — lower = more recent) |

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Bot framework | python-telegram-bot |
| HTTP client | httpx (async) |
| Web search | DuckDuckGo (ddgs library) |
| Reddit | Public JSON API + DDG fallback |
| Twitter/X | twitterapi.io (unofficial API) |
| AI analysis | Moonshot Kimi (kimi-k2.5-preview or later) |
| Email sending | Resend API |
| Hosting | Railway |
| Excel logging | openpyxl |

---

## How It Works — Pipeline

```
1. COLLECT
   ├── Reddit API → r/personalfinancecanada, r/canada, r/banking, etc.
   ├── DuckDuckGo → news, forums, Reddit fallback, RedFlagDeals
   └── twitterapi.io → X/Twitter real-time search

2. FILTER
   ├── Spam/casino domain blocklist
   ├── Low-quality SEO explainer content filter
   ├── Tweet quality filter (length, engagement, signal words)
   └── Recency filter (MAX_MENTION_AGE_DAYS, default 120 days)

3. SORT
   ├── Split into 3 buckets: Reddit (cap 15), Other/RFD (cap 10), Twitter (cap 15)
   └── Each bucket sorted by quality score (upvotes, engagement, keywords, length)

4. ANALYZE (two parallel Kimi AI calls)
   ├── etransfer_chatter_prompt.md → Left column (community chatter)
   └── market_pulse_prompt.md → Right column (market intelligence)

5. DELIVER
   ├── HTML email via Resend → EMAIL_TO recipients
   └── Plain text via Telegram → subscribed chats
```

---

## File Structure

```
teams-sentiment-bot/
├── app.py                      # Everything — fetch, analyze, email, Telegram bot
├── prompts.json                # Search queries + prompt file paths
├── prompts/
│   ├── etransfer_chatter_prompt.md   # Left column AI prompt
│   ├── market_pulse_prompt.md        # Right column AI prompt
│   ├── biweekly_prompt.md            # Legacy (not used for main scan)
│   ├── followup_prompt.md            # Used for /ask follow-up questions
│   └── curation_prompt.md            # Written but not active
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Railway container config
└── Procfile                    # Process start command
```

---

## Telegram Commands

| Command | What it does |
|---|---|
| `/start` | Subscribe to scheduled reports |
| `/status` | Health check — shows all API keys, email config, last scan date |
| `/scan` | Run a full scan right now (takes ~2 minutes) |
| `/email` | Run scan AND send the email digest immediately |
| `/report` | Show the last scan's text output |
| `/ask [question]` | Ask a follow-up question about the last scan |
| `/unsubscribe` | Stop receiving scheduled reports |

---

## Configuring What Gets Monitored

All search queries are in **`prompts.json`** — no code changes needed.

**`etransfer_queries`** — searches for e-Transfer community content (left column source material):
- General queries (no `site:`) → triggers news + Twitter searches via DDG
- `site:reddit.com/r/...` queries → DDG Reddit fallback
- `site:forums.redflagdeals.com` → RedFlagDeals content

**`competitor_queries`** — searches for market/competitor intelligence (right column source material):
- Mix of general queries and `site:reddit.com` for community reactions
- Covers: PayPal, Wise, Wealthsimple, KOHO, Apple Pay, Google Pay, Revolut, Neo Financial, Venmo, Zelle, Square, Stripe

**Rule:** Never add `site:reddit.com` to a general query that you also want to trigger news/Twitter results — the `site:` restriction prevents the news and Twitter follow-up searches from running.

---

## Configuring AI Output Quality

AI prompts are plain markdown files in `prompts/`. Edit them directly to change tone, rules, or output format:

- **`prompts/etransfer_chatter_prompt.md`** — controls left column: what counts as good chatter, quote format, minimum quality bar
- **`prompts/market_pulse_prompt.md`** — controls right column: what counts as market intelligence, exclusion rules, target bullet count

After editing a prompt file, **commit and push to main** — Railway auto-deploys from GitHub.

---

## Schedule

The bot checks daily at 9am EST. It only runs a full scan if 14+ days have passed since the last one (biweekly cadence).

To change the frequency, update `scheduled_biweekly_broadcast()` in `app.py`.  
To force a scan anytime: type `/email` in Telegram.

---

## Redeploying

Railway auto-deploys on every push to `main`. If you need to manually redeploy:
- Railway dashboard → your service → **Deploy** button

If the bot is crash-looping, check Railway logs first. The most common cause is a missing environment variable — `/status` in Telegram will tell you which ones are missing if the bot is running.

---

## Adding a New Email Recipient

In Railway → Variables → `EMAIL_TO` → add the new email separated by a comma:
```
toastud67@gmail.com,newperson@company.com
```

---

## Common Issues & Fixes

| Symptom | Cause | Fix |
|---|---|---|
| Bot not responding in Telegram | Service crashed on Railway | Check Railway logs, look for missing env var |
| `/status` shows wrong time | DST handling — shouldn't happen, uses `ZoneInfo("America/Toronto")` | Check Railway logs for timezone errors |
| Email not sending | Resend API key wrong or domain unverified | Check `/status` for Resend key, verify domain at resend.com/domains |
| `Kimi API 404: model not found` | Model name changed or account has no credits | Update `KIMI_MODEL` in Railway to current model name at platform.moonshot.cn |
| Left column empty | Reddit API blocked on Railway's IP | Reddit DDG fallback queries are in prompts.json — these compensate |
| Left column all one platform | Source bucket imbalance | Caps are set in `fetch_biweekly_mentions()` in app.py — reddit:15, other:10, twitter:15 |
| Casino/gambling content showing | Not in blocklist yet | Add domain to `_BLOCKED_DOMAINS` in app.py |
| Right column thin | Competitor queries not returning results | Add more targeted queries to `competitor_queries` in prompts.json |
| Two bots responding | Old Railway project still running | Suspend/delete old Railway service |

---

## Key Code Locations in app.py

| What | Where |
|---|---|
| All environment variables | Lines ~40–71 |
| Spam domain blocklist | `_BLOCKED_DOMAINS` set |
| Low-quality market content filter | `_is_low_quality_market_content()` |
| Tweet quality filter | Inside `_search_twitter_io()` |
| Quality scoring function | `_mention_quality_score()` |
| Source bucket isolation | Look for `[social-buckets]` comment in `fetch_biweekly_mentions()` |
| Email HTML builder | `_build_biweekly_html()` |
| AI analysis (two Kimi calls) | `analyze_biweekly()` |
| Telegram commands | `cmd_*` functions near bottom of file |
| `/status` command | `cmd_status()` |

---

## What Was Built Over Time (PR History)

| PR | Change |
|---|---|
| #1 | Initial setup |
| #2 | Expanded social fetching, Kimi curation, right column redesign |
| #3 | Market Pulse right column + richer Reddit quotes |
| #4 | Timezone fix (EDT/EST), source diversity, Market Pulse population |
| #5 | Left-side quality: social-only curation, remap NEWS to Market Pulse |
| #6 | Removed over-aggressive curation, restored left-side volume |
| #7 | Split into two independent Kimi calls (one per email column) |
| #8 | Added email/API key diagnostics to `/status` |
| #9 | Added twitterapi.io as real Twitter source |
| #10 | Twitter caps reduced, spam domain blocklist added |
| #11 | Tweet quality filter, unified engagement scoring, right side boost |
| #12 | Reddit DDG fallback queries, tweet filter tightened, right side cleaned |
| #13 | Reddit OAuth (closed — not needed) |
| #14 | Isolated Reddit and Twitter into independent pool buckets |
| #15 | Guaranteed source mix: equal caps, explainer content filter, prompt rules |

---

## If You Need to Start From Scratch

1. Fork https://github.com/utosrad/teams-sentiment-bot
2. Create accounts: Railway, Resend, Moonshot Kimi, twitterapi.io, Telegram BotFather
3. Add all env vars listed above to Railway
4. Verify your sending domain on Resend
5. Push to main → Railway deploys automatically
6. Open Telegram → find your bot → `/start` → `/status` → confirm everything is green → `/email`

---

## Contact

Original developer: Umar Darsot — udarsot@gmail.com  
You have collaborator access on the GitHub repo and access to all accounts under interac.bot@outlook.com.

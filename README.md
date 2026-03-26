# Teams Sentiment Analysis Bot — Deployment Guide

## Prerequisites
- GitHub account
- Railway account (https://railway.app — sign in with GitHub)
- Microsoft 365 admin access (or ask your admin)
- Kimi K2.5 API key from Moonshot (https://platform.moonshot.cn)

---

## Step-by-Step

### 1. Push this repo to GitHub
```bash
cd teams-sentiment-bot
git init
git add .
git commit -m "initial commit"
gh repo create teams-sentiment-bot --private --push
```

### 2. Register a Bot in Azure
1. Go to https://portal.azure.com → search **"Bot Services"** → **Create**
2. Choose **Azure Bot** → **Multi Tenant**
3. Pick **"Create new Microsoft App ID"**
4. Once created, go to the bot resource → **Configuration**:
   - **Messaging endpoint**: leave blank for now (you'll fill this after Railway deploy)
5. Go to **Channels** → **Microsoft Teams** → Enable it
6. Go to the linked **App Registration** → **Certificates & Secrets** → **New client secret** → copy the value
7. Note your **Microsoft App ID** (from the bot overview) and the **secret** you just created

### 3. Deploy to Railway
1. Go to https://railway.app → **New Project** → **Deploy from GitHub Repo**
2. Select your `teams-sentiment-bot` repo
3. Railway auto-detects the Dockerfile. Go to **Settings** → set:
   - **Port**: `3978`
4. Go to **Variables** tab → add:
   ```
   MICROSOFT_APP_ID=<from step 2>
   MICROSOFT_APP_PASSWORD=<client secret from step 2>
   KIMI_API_KEY=<your moonshot api key>
   PORT=3978
   ```
5. Deploy. Railway gives you a URL like `https://teams-sentiment-bot-production-xxxx.up.railway.app`

### 4. Connect Azure Bot to Railway
1. Back in Azure Portal → your Bot → **Configuration**
2. Set **Messaging endpoint** to:
   ```
   https://YOUR-RAILWAY-URL.up.railway.app/api/messages
   ```
3. Save

### 5. Install Bot in Teams
1. Edit `manifest/manifest.json`:
   - Replace `YOUR_MICROSOFT_APP_ID` with your actual App ID (2 places)
   - Replace `your-railway-app.up.railway.app` with your Railway URL (3 places)
2. Add two 32x32 PNG icons as `manifest/color.png` and `manifest/outline.png` (any icons work)
3. Zip the manifest folder contents: `cd manifest && zip ../sentiment-bot.zip *`
4. In Teams → **Apps** → **Manage your apps** → **Upload a custom app** → upload the zip
5. Add the bot to any team/group chat

### 6. Verify
- Send `help` to the bot in Teams → should get command list
- Send any text → should get sentiment analysis back
- Check `/health` endpoint on your Railway URL
- Reports will auto-broadcast at 06:00, 10:00, 14:00, 18:00 UTC

---
## Email Notifications (Optional)
You can also email yourself in three ways:
- Weekly digest (`EMAIL_SEND_MODE=weekly`)
- Alert emails (`EMAIL_SEND_MODE=alert`) for low sentiment or high positive spikes
- On-demand with Telegram `/email` (admin-only)
- Historical deep scan email with Telegram `/deepscan` (admin-only)

### 1. Choose email provider and set Railway variables
In Railway, go to your service → **Settings** → **Variables**, and add at least:

SMTP provider:
```
EMAIL_ENABLED=1
EMAIL_PROVIDER=smtp
EMAIL_SEND_MODE=weekly
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_gmail_app_password
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@yourdomain.com
EMAIL_SUBJECT_PREFIX=Interac Intelligence
EMAIL_WEEKLY_DAY=monday
EMAIL_WEEKLY_HOUR=9
ALERT_HIGH_THRESHOLD=85
```

Resend provider:
```
EMAIL_ENABLED=1
EMAIL_PROVIDER=resend
EMAIL_SEND_MODE=weekly
RESEND_API_KEY=re_xxxxxxxxx
EMAIL_FROM=onboarding@resend.dev
EMAIL_TO=you@yourdomain.com
EMAIL_SUBJECT_PREFIX=Interac Intelligence
EMAIL_WEEKLY_DAY=monday
EMAIL_WEEKLY_HOUR=9
ALERT_HIGH_THRESHOLD=85
```

Notes:
- For Gmail, use an **App Password** (not your regular login password).
- For SMTPS (implicit TLS), set `SMTP_PORT=465`.
- If your SMTP server does not support `STARTTLS`, keep `SMTP_PORT=465` or adjust accordingly.
- For Resend production use, verify your domain and replace `EMAIL_FROM` with your verified sender.

### 2. Control when emails are sent
Current options:
- `EMAIL_SEND_MODE=weekly`: send only the weekly digest at `EMAIL_WEEKLY_DAY` + `EMAIL_WEEKLY_HOUR` (EST).
- `EMAIL_SEND_MODE=alert`: send only alert emails when score is low (`< alert_threshold`) or high (`> ALERT_HIGH_THRESHOLD`).
- `EMAIL_SEND_MODE=always`: shorthand to enable both `weekly` and `alert`.
- `EMAIL_SEND_MODE=weekly,alert`: explicit combined mode (same as `always`).
- `EMAIL_ALERT_DEDUP=1` (default): deduplicate repeated alert type (low/high) and weekly sends in-memory.
- `EMAIL_COOLDOWN_MINUTES=240`: add a time-based cooldown between emails (0 disables).

### 3. What triggers an email?
- **Weekly digest:** weekly scheduler runs at your configured EST day/hour and emails a fresh report.
- **Low alert:** score below `alert_threshold` from `prompts.json`.
- **High spike alert:** score above `ALERT_HIGH_THRESHOLD`.
- **On-demand:** `/email` in Telegram (admin IDs only) runs a fresh scan and sends immediately.
- **Historical deep scan:** `/deepscan` in Telegram (admin IDs only) runs multi-timeframe historical analysis and sends email.

---

## Cost Breakdown
| Service | Cost |
|---------|------|
| Railway (Starter) | ~$5/mo (trial gives $5 free credit) |
| Azure Bot Service | Free tier |
| Kimi K2.5 API | Pay-per-token (very cheap at 4 calls/day) |
| **Total** | **~$5/mo + pennies for Kimi** |

---

## Customization

### Change schedule times
In `app.py`, edit the cron expression:
```python
scheduler.add_job(scheduled_sentiment_broadcast, "cron", hour="6,10,14,18", minute=0)
```

### Prompt files and config locations
- Prompt text now lives in markdown files:
  - `prompts/analysis_prompt.md` (daily lightweight scan)
  - `prompts/followup_prompt.md` (chat follow-ups)
  - `prompts/historical_prompt.md` (historical deep scan)
- Query/config values stay in `prompts.json`:
  - `data_queries` for daily scan
  - `historical_queries` for `/deepscan` timeframes
  - thresholds/lookback/source toggles

### Daily vs historical mode
- Daily monitor (`/scan`, scheduled 4x/day): fast, forward-looking, lightweight.
- Historical deep scan (`/deepscan`): heavier, grouped by RECENT (1mo), MEDIUM (6mo), OLDER (1yr+) and intended for pattern discovery.
- Email rendering is mode-aware: daily reports use the daily template, while `/deepscan` uses a historical layout. If expected sections are missing, emails gracefully fall back to a styled raw report view.

### Plug in your data source
Replace `fetch_data_for_analysis()` in `app.py` with your actual OpenClaw pipeline — Reddit scraping, RSS feeds, database queries, etc.

### Persist conversation references
Currently conversation refs are in-memory (lost on redeploy). For production, swap the `conversation_references` dict with Railway's built-in Redis or Postgres add-on.

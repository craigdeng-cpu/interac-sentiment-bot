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
You can also email yourself occasionally when the bot finds a sentiment drop.

### 1. Add SMTP + recipients as Railway environment variables
In Railway, go to your service → **Settings** → **Variables**, and add at least:
```
EMAIL_ENABLED=1
EMAIL_SEND_MODE=alert
SMTP_HOST=your.smtp.host
SMTP_PORT=587
SMTP_USERNAME=your_smtp_username
SMTP_PASSWORD=your_smtp_password
EMAIL_FROM=you@yourdomain.com
EMAIL_TO=you@yourdomain.com
EMAIL_SUBJECT_PREFIX=Interac Intelligence
```

Notes:
- For SMTPS (implicit TLS), set `SMTP_PORT=465`.
- If your SMTP server does not support `STARTTLS`, keep `SMTP_PORT=465` or adjust accordingly.

### 2. Control when emails are sent
Current options:
- `EMAIL_SEND_MODE=alert` (default): email when sentiment score drops below `alert_threshold` and only once per “alert run”.
- `EMAIL_SEND_MODE=always`: email on every scheduled scan.
- `EMAIL_ALERT_DEDUP=0`: allow repeated emails while still in alert state.
- `EMAIL_COOLDOWN_MINUTES=240`: add a time-based cooldown between emails (0 disables).

### 3. What triggers an email?
The bot already computes `alert_threshold` in `prompts.json`. When the scan result sentiment score is below that threshold, an email is sent.

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

### Plug in your data source
Replace `fetch_data_for_analysis()` in `app.py` with your actual OpenClaw pipeline — Reddit scraping, RSS feeds, database queries, etc.

### Persist conversation references
Currently conversation refs are in-memory (lost on redeploy). For production, swap the `conversation_references` dict with Railway's built-in Redis or Postgres add-on.

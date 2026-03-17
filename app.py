"""
Interac Sentiment Analysis Bot
- Scrapes Reddit, X, RedFlagDeals, news for Interac mentions 4x/day
- Splits people vs press signals
- Alerts on sentiment drops
- Configurable via prompts.json
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
KIMI_API_KEY = os.environ["KIMI_API_KEY"]
KIMI_API_URL = os.environ.get("KIMI_API_URL", "https://api.moonshot.ai/v1/chat/completions")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2.5-preview")
PORT = int(os.environ.get("PORT", 3978))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x}
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "5"))

EST = timezone(timedelta(hours=-5))

subscribed_chats: set[int] = set()
last_report: str = ""
last_mentions_raw: str = ""
last_sentiment_score: int = 50

# Per-user daily rate limiting
user_usage: dict[int, dict] = defaultdict(lambda: {"count": 0, "date": None})


def now_est() -> str:
    return datetime.now(EST).strftime("%Y-%m-%d %I:%M %p EST")


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    if user_id in ADMIN_IDS:
        return True, -1

    today = datetime.now(EST).date()
    usage = user_usage[user_id]

    if usage["date"] != today:
        usage["count"] = 0
        usage["date"] = today

    if usage["count"] >= DAILY_LIMIT:
        return False, 0

    usage["count"] += 1
    return True, DAILY_LIMIT - usage["count"]


# ─── Prompt Config ────────────────────────────────────────────────────────────
def load_prompts() -> dict:
    path = Path(__file__).parent / "prompts.json"
    with open(path) as f:
        return json.load(f)


# ─── Web Scraping ─────────────────────────────────────────────────────────────
async def serper_search(query: str, search_type: str = "search", max_results: int = 5) -> list[dict]:
    if not SERPER_API_KEY:
        return []

    endpoint = {
        "search": "https://google.serper.dev/search",
        "news": "https://google.serper.dev/news",
    }.get(search_type, "https://google.serper.dev/search")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                endpoint,
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": max_results, "tbs": "qdr:d", "gl": "ca"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Serper error for '{query}': {e}")
            return []

    key = "news" if search_type == "news" else "organic"
    results = []
    for item in data.get(key, []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": item.get("source", search_type),
            "date": item.get("date", ""),
        })
    return results


async def search_twitter(query: str, max_results: int = 5) -> list[dict]:
    """Search X/Twitter via Serper."""
    if not SERPER_API_KEY:
        return []

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={
                    "q": f"{query} site:x.com OR site:twitter.com",
                    "num": max_results,
                    "tbs": "qdr:d",
                    "gl": "ca",
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Serper X search error for '{query}': {e}")
            return []

    results = []
    for item in data.get("organic", []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": "X/Twitter",
            "date": item.get("date", ""),
        })
    return results


async def fetch_all_mentions() -> str:
    config = load_prompts()
    queries_config = config["data_queries"]
    sources = config.get("sources", {})
    max_per = config.get("max_mentions_per_source", 5)

    people_mentions = []  # Reddit, X, RFD, forums
    press_mentions = []   # News articles

    all_queries = []
    for category, queries in queries_config.items():
        for q in queries:
            all_queries.append((category, q))

    for category, query in all_queries:
        is_people_category = category == "people_forums"

        # News (press)
        if sources.get("news", True) and not is_people_category:
            results = await serper_search(query, "news", max_per)
            for r in results:
                r["category"] = category
                r["channel"] = "press"
            press_mentions.extend(results)

        # Reddit / forums (people)
        if sources.get("reddit", True) or sources.get("forums", True):
            results = await serper_search(query, "search", max_per)
            for r in results:
                r["category"] = category
                # Classify by domain
                link = r.get("link", "")
                if "reddit.com" in link:
                    r["source"] = "Reddit"
                    r["channel"] = "people"
                elif "redflagdeals.com" in link:
                    r["source"] = "RedFlagDeals"
                    r["channel"] = "people"
                else:
                    r["channel"] = "press"
            people_mentions.extend([r for r in results if r["channel"] == "people"])
            press_mentions.extend([r for r in results if r["channel"] == "press"])

        # X/Twitter (people)
        if sources.get("twitter", True) and not is_people_category:
            results = await search_twitter(query, max_per)
            for r in results:
                r["category"] = category
                r["channel"] = "people"
            people_mentions.extend(results)

    # Deduplicate each pool
    def dedup(mentions):
        seen = set()
        unique = []
        for m in mentions:
            if m["link"] not in seen:
                seen.add(m["link"])
                unique.append(m)
        return unique

    people_mentions = dedup(people_mentions)
    press_mentions = dedup(press_mentions)
    total = len(people_mentions) + len(press_mentions)

    if total == 0:
        return "No recent mentions found across any sources in the last 24 hours."

    lines = [f"=== INTERAC INTELLIGENCE SCAN — {now_est()} ==="]
    lines.append(f"Total: {total} unique mentions ({len(people_mentions)} people, {len(press_mentions)} press)\n")

    if people_mentions:
        lines.append("=== PEOPLE (Reddit, X/Twitter, RedFlagDeals, Forums) ===")
        for i, m in enumerate(people_mentions, 1):
            date_str = f" | Date: {m['date']}" if m.get("date") else ""
            lines.append(
                f"[P{i}] {m['title']}\n"
                f"    Source: {m['source']}{date_str}\n"
                f"    Snippet: {m['snippet']}\n"
                f"    URL: {m['link']}\n"
            )

    if press_mentions:
        lines.append("=== PRESS & INDUSTRY ===")
        for i, m in enumerate(press_mentions, 1):
            date_str = f" | Date: {m['date']}" if m.get("date") else ""
            lines.append(
                f"[N{i}] {m['title']}\n"
                f"    Source: {m['source']}{date_str}\n"
                f"    Snippet: {m['snippet']}\n"
                f"    URL: {m['link']}\n"
            )

    return "\n".join(lines)


# ─── Kimi K2.5 Analysis ──────────────────────────────────────────────────────
async def call_kimi(system_prompt: str, user_content: str) -> str:
    # Truncate to ~60k chars to stay within Kimi's token limits
    if len(user_content) > 60000:
        user_content = user_content[:60000] + "\n\n[... truncated due to length]"

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            KIMI_API_URL,
            headers={
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": KIMI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.3,
            },
        )
        if response.status_code != 200:
            body = response.text
            logger.error(f"Kimi API {response.status_code}: {body}")
            raise Exception(f"Kimi API {response.status_code}: {body[:300]}")
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def analyze_sentiment(mentions_text: str) -> str:
    config = load_prompts()
    prompt = config["analysis_prompt"].replace("{timestamp}", now_est())
    return await call_kimi(prompt, mentions_text)


def extract_sentiment_score(report: str) -> int:
    """Parse sentiment score from report text."""
    for line in report.split("\n"):
        if "SENTIMENT SCORE" in line.upper():
            for part in line.split():
                try:
                    score = int(part)
                    if 0 <= score <= 100:
                        return score
                except ValueError:
                    continue
    return 50


async def ask_followup(question: str, report_context: str) -> str:
    config = load_prompts()
    return await call_kimi(
        config["followup_prompt"],
        f"Latest report:\n{report_context}\n\nRaw mentions:\n{last_mentions_raw[:3000]}\n\nQuestion: {question}",
    )


# ─── Scheduled Broadcast ─────────────────────────────────────────────────────
async def scheduled_sentiment_broadcast(context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw, last_sentiment_score
    logger.info(f"[{now_est()}] Running scheduled Interac sentiment scan...")

    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report

        score = extract_sentiment_score(report)
        config = load_prompts()
        threshold = config.get("alert_threshold", 35)

        # Check for alert
        alert_prefix = ""
        if score < threshold:
            alert_prefix = f"🚨 *ALERT: Sentiment dropped to {score}/100* 🚨\n\n"

        last_sentiment_score = score
        message = f"{alert_prefix}📊 *Interac Intelligence* — {now_est()}\n\n{report}"

        for chat_id in subscribed_chats.copy():
            try:
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")
                subscribed_chats.discard(chat_id)
    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


# ─── Command Handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.add(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Interac Intelligence Bot*\n\n"
        "Scans Reddit, X, RedFlagDeals, and news for Interac signals 4x/day.\n\n"
        "*Commands:*\n"
        "• /subscribe — Get scheduled reports\n"
        "• /unsubscribe — Stop reports\n"
        "• /scan — Run a scan now\n"
        "• /raw — See raw mentions from last scan\n"
        "• /prompt — View current config\n"
        "• /status — Check schedule\n"
        "• Any text → Follow-up on latest report",
        parse_mode="Markdown",
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.add(update.effective_chat.id)
    await update.message.reply_text("✅ Subscribed.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.discard(update.effective_chat.id)
    await update.message.reply_text("🔕 Unsubscribed.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    has_serper = "✅" if SERPER_API_KEY else "❌"
    config = load_prompts()
    q_count = sum(len(v) for v in config["data_queries"].values())
    is_admin = "✅" if update.effective_user.id in ADMIN_IDS else "❌"
    await update.message.reply_text(
        f"✅ Bot running — {now_est()}\n"
        f"Serper API: {has_serper}\n"
        f"Active queries: {q_count}\n"
        f"Last sentiment score: {last_sentiment_score}/100\n"
        f"Reports at 6am, 10am, 2pm, 6pm EST\n"
        f"Subscribed: {len(subscribed_chats)}\n"
        f"Admin: {is_admin}\n"
        f"Your ID: `{update.effective_user.id}`"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw, last_sentiment_score
    await update.message.reply_text("🔍 Scanning Reddit, X, RedFlagDeals, news...")

    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report

        score = extract_sentiment_score(report)
        last_sentiment_score = score

        config = load_prompts()
        threshold = config.get("alert_threshold", 35)
        alert_prefix = f"🚨 *ALERT: Sentiment {score}/100* 🚨\n\n" if score < threshold else ""

        await update.message.reply_text(
            f"{alert_prefix}📊 *Interac Intelligence* — {now_est()}\n\n{report}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        await update.message.reply_text(f"❌ Scan failed: {e}")


async def cmd_raw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not last_mentions_raw:
        await update.message.reply_text("No scan data yet. Run /scan first.")
        return
    text = last_mentions_raw[:4000]
    await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_prompts()
    queries = config["data_queries"]
    summary = "\n".join(f"*{k}:* {len(v)} queries" for k, v in queries.items())
    sources = config.get("sources", {})
    active = ", ".join(k for k, v in sources.items() if v)
    await update.message.reply_text(
        f"*Query categories:*\n{summary}\n\n"
        f"*Active sources:* {active}\n"
        f"*Alert threshold:* <{config.get('alert_threshold', 35)}/100\n\n"
        f"Edit `prompts.json` to change.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return
    if not last_report:
        await update.message.reply_text("No report yet. Run /scan first.")
        return

    allowed, remaining = check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(
            f"⚠️ Daily limit reached ({DAILY_LIMIT} questions/day). Resets at midnight EST."
        )
        return

    await update.message.reply_text("🤔 Thinking...")
    try:
        response = await ask_followup(user_text, last_report)
        suffix = f"\n\n_({remaining} questions remaining today)_" if remaining >= 0 else ""
        await update.message.reply_text(response + suffix, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("raw", cmd_raw))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 6am, 10am, 2pm, 6pm EST = 11, 15, 19, 23 UTC
    job_queue = app.job_queue
    for utc_hour in [11, 15, 19, 23]:
        job_queue.run_daily(
            scheduled_sentiment_broadcast,
            time=datetime.strptime(f"{utc_hour:02d}:00", "%H:%M").time(),
            name=f"sentiment_{utc_hour:02d}",
        )

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="webhook",
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()

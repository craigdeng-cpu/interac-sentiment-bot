"""
Interac Sentiment Analysis Bot
- Scrapes internet for Interac product/FI/competitive mentions 4x/day
- Analyzes via Kimi K2.5 with strict signal-only reporting
- Configurable prompts via prompts.json
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

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

EST = timezone(timedelta(hours=-5))

subscribed_chats: set[int] = set()
last_report: str = ""
last_mentions_raw: str = ""


def now_est() -> str:
    return datetime.now(EST).strftime("%Y-%m-%d %I:%M %p EST")


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
    return [
        {
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": item.get("source", search_type),
        }
        for item in data.get(key, [])
    ]


async def fetch_all_mentions() -> str:
    config = load_prompts()
    queries_config = config["data_queries"]
    sources = config.get("sources", {})
    max_per = config.get("max_mentions_per_source", 5)

    all_mentions = []

    # Flatten all query categories
    all_queries = []
    for category, queries in queries_config.items():
        for q in queries:
            all_queries.append((category, q))

    for category, query in all_queries:
        if sources.get("news", True):
            results = await serper_search(query, "news", max_per)
            for r in results:
                r["category"] = category
            all_mentions.extend(results)

        if sources.get("reddit", True):
            results = await serper_search(f"{query} site:reddit.com", "search", max_per)
            for r in results:
                r["category"] = category
                r["source"] = "Reddit"
            all_mentions.extend(results)

    # Deduplicate by link
    seen = set()
    unique = []
    for m in all_mentions:
        if m["link"] not in seen:
            seen.add(m["link"])
            unique.append(m)

    if not unique:
        return "No recent mentions found across any sources in the last 24 hours."

    # Group by category for cleaner LLM input
    by_cat = {}
    for m in unique:
        cat = m.get("category", "other")
        by_cat.setdefault(cat, []).append(m)

    lines = [f"=== INTERAC INTELLIGENCE SCAN — {now_est()} ==="]
    lines.append(f"Total unique mentions: {len(unique)}\n")

    for cat, mentions in by_cat.items():
        label = cat.upper().replace("_", " ")
        lines.append(f"--- {label} ({len(mentions)} mentions) ---")
        for i, m in enumerate(mentions, 1):
            lines.append(
                f"[{i}] {m['title']}\n"
                f"    Source: {m['source']}\n"
                f"    Snippet: {m['snippet']}\n"
                f"    URL: {m['link']}\n"
            )
        lines.append("")

    return "\n".join(lines)


# ─── Kimi K2.5 Analysis ──────────────────────────────────────────────────────
async def call_kimi(system_prompt: str, user_content: str) -> str:
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
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def analyze_sentiment(mentions_text: str) -> str:
    config = load_prompts()
    prompt = config["analysis_prompt"].replace("{timestamp}", now_est())
    return await call_kimi(prompt, mentions_text)


async def ask_followup(question: str, report_context: str) -> str:
    config = load_prompts()
    return await call_kimi(
        config["followup_prompt"],
        f"Latest report:\n{report_context}\n\nRaw mentions:\n{last_mentions_raw[:3000]}\n\nQuestion: {question}",
    )


# ─── Scheduled Broadcast ─────────────────────────────────────────────────────
async def scheduled_sentiment_broadcast(context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw
    logger.info(f"[{now_est()}] Running scheduled Interac sentiment scan...")

    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report

        message = f"📊 *Interac Intelligence* — {now_est()}\n\n{report}"

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
        "Scans internet 4x/day for Interac product signals across all major Canadian FIs.\n\n"
        "*Commands:*\n"
        "• /subscribe — Get scheduled reports\n"
        "• /unsubscribe — Stop reports\n"
        "• /scan — Run a scan now\n"
        "• /raw — See raw mentions from last scan\n"
        "• /prompt — View current config\n"
        "• /status — Check schedule\n"
        "• Any text → Follow-up question on latest report",
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
    await update.message.reply_text(
        f"✅ Bot running — {now_est()}\n"
        f"Serper API: {has_serper}\n"
        f"Active queries: {q_count}\n"
        f"Reports at 06:00, 10:00, 14:00, 18:00 EST\n"
        f"Subscribed: {len(subscribed_chats)}"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw
    await update.message.reply_text("🔍 Scanning...")

    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report

        await update.message.reply_text(
            f"📊 *Interac Intelligence* — {now_est()}\n\n{report}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        await update.message.reply_text(f"❌ Scan failed: {e}")


async def cmd_raw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not last_mentions_raw:
        await update.message.reply_text("No scan data yet. Run /scan first.")
        return
    # Telegram max message is 4096 chars
    text = last_mentions_raw[:4000]
    await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_prompts()
    queries = config["data_queries"]
    summary = "\n".join(f"*{k}:* {len(v)} queries" for k, v in queries.items())
    await update.message.reply_text(
        f"*Query categories:*\n{summary}\n\n"
        f"Edit `prompts.json` to change queries or prompts.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return
    if not last_report:
        await update.message.reply_text("No report yet. Run /scan first.")
        return

    await update.message.reply_text("🤔 Thinking...")
    try:
        response = await ask_followup(user_text, last_report)
        await update.message.reply_text(response, parse_mode="Markdown")
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

    # Schedule 4x/day in EST (convert to UTC for the scheduler)
    # EST = UTC-5, so 6am/10am/2pm/6pm EST = 11/15/19/23 UTC
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

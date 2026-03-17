"""
Interac Sentiment Analysis Bot
- Scrapes internet for Interac mentions 4x/day
- Analyzes sentiment via Kimi K2.5
- Broadcasts 1-min executive report to Telegram
- Configurable prompts via prompts.json
"""

import os
import json
import logging
from datetime import datetime
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
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")  # google search API

subscribed_chats: set[int] = set()
last_report: str = ""  # cache last report for follow-ups


# ─── Prompt Config ────────────────────────────────────────────────────────────
def load_prompts() -> dict:
    """Load prompts from prompts.json. Reloads on every call so edits are live."""
    path = Path(__file__).parent / "prompts.json"
    with open(path) as f:
        return json.load(f)


# ─── Web Scraping: Gather Interac Mentions ────────────────────────────────────
async def search_web(query: str, max_results: int = 10) -> list[dict]:
    """Search via Serper.dev (Google Search API). $0.001/search."""
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set, using fallback")
        return []

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": max_results, "tbs": "qdr:d"},  # last 24h
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("organic", []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": item.get("source", ""),
        })
    return results


async def search_reddit(query: str, max_results: int = 10) -> list[dict]:
    """Search Reddit via Serper for recent mentions."""
    if not SERPER_API_KEY:
        return []

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": f"{query} site:reddit.com", "num": max_results, "tbs": "qdr:d"},
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("organic", []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": "Reddit",
        })
    return results


async def search_news(query: str, max_results: int = 10) -> list[dict]:
    """Search Google News via Serper."""
    if not SERPER_API_KEY:
        return []

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://google.serper.dev/news",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": max_results, "tbs": "qdr:d"},
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("news", []):
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
            "source": item.get("source", "News"),
        })
    return results


async def fetch_all_mentions() -> str:
    """Gather all Interac mentions across sources into a single text block."""
    config = load_prompts()
    queries = config["data_queries"]
    sources = config.get("sources", {})
    max_per_source = config.get("max_mentions_per_source", 10)

    all_mentions = []

    for query in queries:
        if sources.get("news", True):
            mentions = await search_news(query, max_results=max_per_source)
            all_mentions.extend(mentions)

        if sources.get("reddit", True):
            mentions = await search_reddit(query, max_results=max_per_source)
            all_mentions.extend(mentions)

        if sources.get("twitter", False):
            # Twitter/X API requires separate auth — placeholder
            pass

    # Deduplicate by link
    seen = set()
    unique = []
    for m in all_mentions:
        if m["link"] not in seen:
            seen.add(m["link"])
            unique.append(m)

    if not unique:
        return "No recent mentions found in the last 24 hours."

    # Format for the LLM
    lines = [f"=== INTERAC ONLINE MENTIONS ({len(unique)} found) ===\n"]
    for i, m in enumerate(unique, 1):
        lines.append(
            f"[{i}] {m['title']}\n"
            f"    Source: {m['source']}\n"
            f"    Snippet: {m['snippet']}\n"
            f"    URL: {m['link']}\n"
        )

    return "\n".join(lines)


# ─── Kimi K2.5 Analysis ──────────────────────────────────────────────────────
async def analyze_sentiment(mentions_text: str) -> str:
    """Send mentions to Kimi K2.5 with the configured analysis prompt."""
    config = load_prompts()

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
                    {"role": "system", "content": config["analysis_prompt"]},
                    {"role": "user", "content": mentions_text},
                ],
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def ask_followup(question: str, report_context: str) -> str:
    """Handle follow-up questions with context from the last report."""
    config = load_prompts()

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            KIMI_API_URL,
            headers={
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": KIMI_MODEL,
                "messages": [
                    {"role": "system", "content": config["followup_prompt"]},
                    {"role": "user", "content": f"Latest report:\n{report_context}\n\nQuestion: {question}"},
                ],
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


# ─── Scheduled Broadcast ─────────────────────────────────────────────────────
async def scheduled_sentiment_broadcast(context: ContextTypes.DEFAULT_TYPE):
    global last_report
    logger.info(f"[{datetime.utcnow()}] Running scheduled Interac sentiment scan...")

    try:
        mentions = await fetch_all_mentions()
        report = await analyze_sentiment(mentions)
        last_report = report

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        message = f"📊 *Interac Sentiment Report* — {timestamp}\n\n{report}"

        for chat_id in subscribed_chats.copy():
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")
                subscribed_chats.discard(chat_id)
    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


# ─── Command Handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.add(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Interac Sentiment Bot*\n\n"
        "I scan the internet for Interac mentions 4x/day and deliver a 1-min sentiment report.\n\n"
        "*Commands:*\n"
        "• /subscribe — Get scheduled reports\n"
        "• /unsubscribe — Stop reports\n"
        "• /scan — Run a scan right now\n"
        "• /prompt — View the current analysis prompt\n"
        "• /status — Check schedule\n"
        "• Any text → Ask a follow-up about the latest report",
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
    await update.message.reply_text(
        f"✅ Bot running\n"
        f"Serper API: {has_serper}\n"
        f"Reports at 06:00, 10:00, 14:00, 18:00 UTC\n"
        f"Subscribed chats: {len(subscribed_chats)}"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual scan — run sentiment analysis on demand."""
    global last_report
    await update.message.reply_text("🔍 Scanning internet for Interac mentions...")

    try:
        mentions = await fetch_all_mentions()
        report = await analyze_sentiment(mentions)
        last_report = report

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        await update.message.reply_text(
            f"📊 *Interac Sentiment Report* — {timestamp}\n\n{report}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Manual scan failed: {e}")
        await update.message.reply_text(f"❌ Scan failed: {e}")


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current analysis prompt so user can verify/tweak."""
    config = load_prompts()
    await update.message.reply_text(
        f"*Current analysis prompt:*\n\n`{config['analysis_prompt'][:1000]}`\n\n"
        f"*Search queries:*\n{', '.join(config['data_queries'])}\n\n"
        f"Edit `prompts.json` and redeploy to change.",
        parse_mode="Markdown",
    )


# ─── Follow-up Handler ───────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    if not last_report:
        await update.message.reply_text(
            "No report yet. Run /scan first or wait for the next scheduled report."
        )
        return

    await update.message.reply_text("🤔 Thinking...")
    try:
        response = await ask_followup(user_text, last_report)
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Follow-up error: {e}")
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
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = app.job_queue
    for hour in [6, 10, 14, 18]:
        job_queue.run_daily(
            scheduled_sentiment_broadcast,
            time=datetime.strptime(f"{hour:02d}:00", "%H:%M").time(),
            name=f"sentiment_{hour:02d}",
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

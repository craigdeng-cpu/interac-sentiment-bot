"""
Telegram Sentiment Analysis Bot
- Runs sentiment analysis 4x/day via APScheduler
- Two-way chat with users via Telegram Bot API
- Uses OpenClaw + Kimi K2.5 for inference
"""

import os
import json
import logging
from datetime import datetime

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
KIMI_API_URL = os.environ.get("KIMI_API_URL", "https://api.moonshot.cn/v1/chat/completions")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2.5")
PORT = int(os.environ.get("PORT", 3978))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # e.g. https://your-app.up.railway.app

# Chat IDs that receive scheduled broadcasts
subscribed_chats: set[int] = set()


# ─── Kimi K2.5 / OpenClaw Sentiment ──────────────────────────────────────────
async def run_sentiment_analysis(text: str) -> dict:
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
                    {
                        "role": "system",
                        "content": (
                            "You are a sentiment analysis assistant. "
                            "Analyze the sentiment of the provided text. "
                            "Return a JSON object with keys: "
                            "'sentiment' (positive/negative/neutral/mixed), "
                            "'confidence' (0-1), "
                            "'summary' (1-2 sentence explanation)."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"sentiment": "unknown", "confidence": 0, "summary": content}


# ─── Data Source ──────────────────────────────────────────────────────────────
async def fetch_data_for_analysis() -> str:
    """
    TODO: Replace with your actual OpenClaw data pipeline.
    Reddit scraping, RSS feeds, database queries, etc.
    """
    return (
        "Sample text for sentiment analysis. "
        "Replace this with your OpenClaw data source."
    )


# ─── Scheduled Job: 4x/day Broadcast ─────────────────────────────────────────
async def scheduled_sentiment_broadcast(context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"[{datetime.utcnow()}] Running scheduled sentiment analysis...")
    try:
        text = await fetch_data_for_analysis()
        result = await run_sentiment_analysis(text)

        message = (
            f"📊 *Scheduled Sentiment Report* — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"*Sentiment:* {result.get('sentiment', 'N/A')}\n"
            f"*Confidence:* {result.get('confidence', 'N/A')}\n"
            f"*Summary:* {result.get('summary', 'N/A')}\n\n"
            f"_Reply to ask follow-up questions._"
        )

        for chat_id in subscribed_chats.copy():
            try:
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
                logger.info(f"Sent report to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")
                subscribed_chats.discard(chat_id)
    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


# ─── Command Handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.add(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Sentiment Analysis Bot*\n\n"
        "I run sentiment analysis 4x/day and broadcast results here.\n\n"
        "*Commands:*\n"
        "• /subscribe — Get scheduled reports in this chat\n"
        "• /unsubscribe — Stop reports\n"
        "• /status — Check schedule\n"
        "• Send any text → I'll analyze its sentiment",
        parse_mode="Markdown",
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.add(update.effective_chat.id)
    await update.message.reply_text("✅ Subscribed to scheduled sentiment reports.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_chats.discard(update.effective_chat.id)
    await update.message.reply_text("🔕 Unsubscribed from scheduled reports.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is running.\n"
        "Reports broadcast at 06:00, 10:00, 14:00, 18:00 UTC.\n"
        f"Subscribed chats: {len(subscribed_chats)}"
    )


# ─── Message Handler (ad-hoc sentiment) ──────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    await update.message.reply_text("🔍 Analyzing sentiment...")
    try:
        result = await run_sentiment_analysis(user_text)
        response = (
            f"*Sentiment:* {result.get('sentiment', 'N/A')}\n"
            f"*Confidence:* {result.get('confidence', 'N/A')}\n"
            f"*Summary:* {result.get('summary', 'N/A')}"
        )
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await update.message.reply_text(f"❌ Analysis failed: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))

    # Any text message → sentiment analysis
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule 4x/day broadcasts (06:00, 10:00, 14:00, 18:00 UTC)
    job_queue = app.job_queue
    for hour in [6, 10, 14, 18]:
        job_queue.run_daily(
            scheduled_sentiment_broadcast,
            time=datetime.strptime(f"{hour:02d}:00", "%H:%M").time(),
            name=f"sentiment_{hour:02d}",
        )

    if WEBHOOK_URL:
        # Webhook mode (for Railway / production)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="webhook",
        )
    else:
        # Polling mode (for local dev)
        app.run_polling()


if __name__ == "__main__":
    main()

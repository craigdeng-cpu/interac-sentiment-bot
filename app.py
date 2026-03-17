"""
Teams Sentiment Analysis Bot
- Runs sentiment analysis 4x/day via APScheduler
- Two-way chat with users via Bot Framework
- Uses OpenClaw + Kimi K2.5 for inference
"""

import os
import json
import logging
from datetime import datetime

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity, ActivityTypes
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
KIMI_API_URL = os.environ.get("KIMI_API_URL", "https://api.moonshot.cn/v1/chat/completions")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2.5")
PORT = int(os.environ.get("PORT", 3978))

# Store conversation references for proactive messaging
conversation_references: dict[str, object] = {}

# ─── Bot Framework Adapter ────────────────────────────────────────────────────
settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(settings)


async def on_error(context: TurnContext, error: Exception):
    logger.error(f"Bot error: {error}")
    await context.send_activity("Sorry, something went wrong.")

adapter.on_turn_error = on_error


# ─── Kimi K2.5 / OpenClaw Sentiment ──────────────────────────────────────────
async def run_sentiment_analysis(text: str) -> dict:
    """
    Call Kimi K2.5 via OpenClaw for sentiment analysis.
    Adjust the prompt/system message to match your OpenClaw pipeline.
    """
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

        # Try to parse JSON from response
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"sentiment": "unknown", "confidence": 0, "summary": content}


# ─── Data Source ──────────────────────────────────────────────────────────────
async def fetch_data_for_analysis() -> str:
    """
    TODO: Replace this with your actual data source.
    Examples: scrape Reddit/forums, pull from a database, read RSS feeds, etc.
    This is where OpenClaw pipeline fetches the content to analyze.
    """
    # Placeholder — replace with your data pipeline
    return (
        "Sample text for sentiment analysis. "
        "Replace this with your OpenClaw data source — "
        "Reddit posts, forum threads, news articles, etc."
    )


# ─── Scheduled Job: 4x/day Broadcast ─────────────────────────────────────────
async def scheduled_sentiment_broadcast():
    """Runs 4x/day. Fetches data, analyzes sentiment, broadcasts to all registered chats."""
    logger.info(f"[{datetime.utcnow()}] Running scheduled sentiment analysis...")

    try:
        text = await fetch_data_for_analysis()
        result = await run_sentiment_analysis(text)

        message = (
            f"📊 **Scheduled Sentiment Report** — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"**Sentiment:** {result.get('sentiment', 'N/A')}\n"
            f"**Confidence:** {result.get('confidence', 'N/A')}\n"
            f"**Summary:** {result.get('summary', 'N/A')}\n\n"
            f"_Reply to this message to ask follow-up questions._"
        )

        # Send to all registered conversations
        for ref_key, ref in conversation_references.items():
            try:
                await adapter.continue_conversation(
                    ref,
                    lambda turn_context: turn_context.send_activity(message),
                    MICROSOFT_APP_ID,
                )
                logger.info(f"Sent report to conversation {ref_key}")
            except Exception as e:
                logger.error(f"Failed to send to {ref_key}: {e}")

    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


# ─── Bot Message Handler ─────────────────────────────────────────────────────
async def on_message(turn_context: TurnContext):
    """Handle incoming Teams messages (two-way chat)."""

    # Save conversation reference for proactive messaging
    ref = TurnContext.get_conversation_reference(turn_context.activity)
    conversation_references[ref.conversation.id] = ref

    user_text = turn_context.activity.text or ""

    if user_text.strip().lower() in ("hi", "hello", "help", "start"):
        await turn_context.send_activity(
            "👋 **Sentiment Analysis Bot**\n\n"
            "I run sentiment analysis 4x/day and broadcast results here.\n\n"
            "**Commands:**\n"
            "• Send any text → I'll analyze its sentiment\n"
            "• `status` → Check when the next report runs\n"
            "• `help` → Show this message"
        )
        return

    if user_text.strip().lower() == "status":
        await turn_context.send_activity(
            "✅ Bot is running. Sentiment reports are sent at 06:00, 10:00, 14:00, 18:00 UTC."
        )
        return

    # Default: run sentiment analysis on whatever the user sent
    await turn_context.send_activity("🔍 Analyzing sentiment...")

    try:
        result = await run_sentiment_analysis(user_text)
        response = (
            f"**Sentiment:** {result.get('sentiment', 'N/A')}\n"
            f"**Confidence:** {result.get('confidence', 'N/A')}\n"
            f"**Summary:** {result.get('summary', 'N/A')}"
        )
        await turn_context.send_activity(response)
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await turn_context.send_activity(f"❌ Analysis failed: {str(e)}")


# ─── Web Server (aiohttp) ────────────────────────────────────────────────────
async def messages(req: web.Request) -> web.Response:
    """Bot Framework messages endpoint."""
    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    body = await req.json()
    activity = Activity().deserialize(body)

    auth_header = req.headers.get("Authorization", "")

    async def call_bot(turn_context: TurnContext):
        if turn_context.activity.type == ActivityTypes.message:
            await on_message(turn_context)
        elif turn_context.activity.type == ActivityTypes.conversation_update:
            # Auto-register when bot is added to a chat/channel
            for member in turn_context.activity.members_added or []:
                if member.id != turn_context.activity.recipient.id:
                    ref = TurnContext.get_conversation_reference(turn_context.activity)
                    conversation_references[ref.conversation.id] = ref
                    await turn_context.send_activity(
                        "👋 Sentiment Analysis Bot is now active in this chat! "
                        "Type `help` for commands."
                    )

    await adapter.process_activity(activity, auth_header, call_bot)
    return web.Response(status=201)


async def health(req: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "conversations": len(conversation_references)})


# ─── App Startup ──────────────────────────────────────────────────────────────
app = web.Application()
app.router.add_post("/api/messages", messages)
app.router.add_get("/health", health)

# Schedule sentiment analysis 4x/day (06:00, 10:00, 14:00, 18:00 UTC)
scheduler = AsyncIOScheduler()
scheduler.add_job(scheduled_sentiment_broadcast, "cron", hour="6,10,14,18", minute=0)


async def on_startup(app):
    scheduler.start()
    logger.info("Scheduler started — sentiment reports at 06:00, 10:00, 14:00, 18:00 UTC")


app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)

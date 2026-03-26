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
import smtplib
import asyncio
import re
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

EMAIL_ENABLED = os.environ.get("EMAIL_ENABLED", "0") == "1"
EMAIL_SEND_MODE = os.environ.get("EMAIL_SEND_MODE", "alert").lower()
EMAIL_ALERT_DEDUP = os.environ.get("EMAIL_ALERT_DEDUP", "1") == "1"
EMAIL_COOLDOWN_MINUTES = int(os.environ.get("EMAIL_COOLDOWN_MINUTES", "0"))
EMAIL_WEEKLY_DAY = os.environ.get("EMAIL_WEEKLY_DAY", "monday").strip().lower()
EMAIL_WEEKLY_HOUR = int(os.environ.get("EMAIL_WEEKLY_HOUR", "9"))
ALERT_HIGH_THRESHOLD = int(os.environ.get("ALERT_HIGH_THRESHOLD", "85"))
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "smtp").strip().lower()

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_API_URL = os.environ.get("RESEND_API_URL", "https://api.resend.com/emails")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = [x.strip() for x in os.environ.get("EMAIL_TO", "").split(",") if x.strip()]
EMAIL_SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", "Interac Intelligence")

EST = timezone(timedelta(hours=-5))

subscribed_chats: set[int] = set()
last_report: str = ""
last_mentions_raw: str = ""
last_sentiment_score: int = 50
last_alert_kind: str | None = None
last_email_sent_at: datetime | None = None
last_weekly_email_key: str | None = None

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
    base_dir = Path(__file__).parent
    config_path = base_dir / "prompts.json"
    with open(config_path) as f:
        config = json.load(f)

    prompt_files = config.get("prompt_files", {})
    default_prompt_files = {
        "analysis_prompt": "prompts/analysis_prompt.md",
        "followup_prompt": "prompts/followup_prompt.md",
    }
    for prompt_key, default_path in default_prompt_files.items():
        rel_path = prompt_files.get(prompt_key, default_path)
        prompt_path = base_dir / rel_path
        if prompt_path.exists():
            config[prompt_key] = prompt_path.read_text().strip()
        elif prompt_key not in config:
            raise FileNotFoundError(f"Missing prompt file for {prompt_key}: {prompt_path}")

    # Optional extra prompts (e.g. historical_prompt).
    for prompt_key, rel_path in prompt_files.items():
        if prompt_key in config:
            continue
        if not prompt_key.endswith("_prompt"):
            continue
        prompt_path = base_dir / rel_path
        if prompt_path.exists():
            config[prompt_key] = prompt_path.read_text().strip()
        else:
            raise FileNotFoundError(f"Missing prompt file for {prompt_key}: {prompt_path}")

    return config


# ─── Web Scraping ─────────────────────────────────────────────────────────────
def lookback_hours_to_tbs(lookback_hours: int) -> str:
    # Serper/Google time filters: qdr:d (day), qdr:w (week), qdr:m (month).
    if lookback_hours <= 24:
        return "qdr:d"
    if lookback_hours <= 24 * 7:
        return "qdr:w"
    return "qdr:m"


def normalize_tbs(tbs: str) -> str:
    supported = {"qdr:d", "qdr:w", "qdr:m", "qdr:y"}
    return tbs if tbs in supported else "qdr:m"


async def serper_search(
    query: str,
    search_type: str = "search",
    max_results: int = 5,
    tbs: str = "qdr:w",
) -> list[dict]:
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
                json={"q": query, "num": max_results, "tbs": tbs, "gl": "ca"},
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


async def search_twitter(query: str, max_results: int = 5, tbs: str = "qdr:w") -> list[dict]:
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
                    "tbs": tbs,
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
    lookback_hours = int(config.get("lookback_hours", 72))
    tbs = lookback_hours_to_tbs(lookback_hours)

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
            results = await serper_search(query, "news", max_per, tbs=tbs)
            for r in results:
                r["category"] = category
                r["channel"] = "press"
            press_mentions.extend(results)

        # Reddit / forums (people)
        if sources.get("reddit", True) or sources.get("forums", True):
            results = await serper_search(query, "search", max_per, tbs=tbs)
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
            results = await search_twitter(query, max_per, tbs=tbs)
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
        return f"No recent mentions found across any sources in the last {lookback_hours} hours."

    lines = [f"=== INTERAC INTELLIGENCE SCAN — {now_est()} ==="]
    lines.append(f"Total: {total} unique mentions ({len(people_mentions)} people, {len(press_mentions)} press)\n")

    if people_mentions:
        lines.append("=== PEOPLE (Reddit, X, RFD) ===")
        for i, m in enumerate(people_mentions[:15], 1):
            date_str = f" ({m['date']})" if m.get("date") else ""
            lines.append(f"[P{i}] {m['title']} | {m['source']}{date_str}\n  {m['snippet'][:150]}\n  {m['link']}")

    if press_mentions:
        lines.append("\n=== PRESS ===")
        for i, m in enumerate(press_mentions[:10], 1):
            date_str = f" ({m['date']})" if m.get("date") else ""
            lines.append(f"[N{i}] {m['title']} | {m['source']}{date_str}\n  {m['snippet'][:150]}\n  {m['link']}")

    return "\n".join(lines)


async def fetch_historical_mentions() -> str:
    config = load_prompts()
    historical_queries = config.get("historical_queries", {})
    max_per = config.get("max_mentions_per_source", 5)

    if not historical_queries:
        return "No historical query config found."

    lines = [f"=== INTERAC HISTORICAL SCAN — {now_est()} ==="]

    for timeframe_key, block in historical_queries.items():
        label = block.get("label", timeframe_key)
        tbs = normalize_tbs(block.get("tbs", "qdr:m"))
        queries = block.get("queries", [])
        mentions = []

        for query in queries:
            news_results = await serper_search(query, "news", max_per, tbs=tbs)
            search_results = await serper_search(query, "search", max_per, tbs=tbs)
            x_results = await search_twitter(query, max_per, tbs=tbs)

            for r in news_results:
                r["source"] = r.get("source", "News")
                mentions.append(r)
            for r in search_results:
                link = r.get("link", "")
                if "reddit.com" in link:
                    r["source"] = "Reddit"
                elif "redflagdeals.com" in link:
                    r["source"] = "RedFlagDeals"
                mentions.append(r)
            mentions.extend(x_results)

        seen = set()
        unique = []
        for m in mentions:
            link = m.get("link", "")
            if not link or link in seen:
                continue
            seen.add(link)
            unique.append(m)

        lines.append(f"\n=== {label} | tbs={tbs} | mentions={len(unique)} ===")
        for i, m in enumerate(unique[:12], 1):
            date_str = f" ({m.get('date')})" if m.get("date") else ""
            lines.append(
                f"[H{i}] {m.get('title', '')} | {m.get('source', 'unknown')}{date_str}\n"
                f"  {m.get('snippet', '')[:150]}\n"
                f"  {m.get('link', '')}"
            )

    return "\n".join(lines)


# ─── Kimi K2.5 Analysis ──────────────────────────────────────────────────────
async def call_kimi(system_prompt: str, user_content: str) -> str:
    # 8192 token limit total. System prompt ~500 tokens, output ~800 tokens.
    # Budget ~6000 tokens (~18k chars) for user content. Cap at 15k for safety.
    if len(user_content) > 15000:
        user_content = user_content[:15000] + "\n\n[... truncated]"

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
                "max_tokens": 2000,
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


async def analyze_historical(mentions_text: str) -> str:
    config = load_prompts()
    prompt = config["historical_prompt"].replace("{timestamp}", now_est())
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


def parse_email_modes() -> set[str]:
    # Supports: alert, weekly, always, comma-separated combinations.
    modes = {m.strip().lower() for m in EMAIL_SEND_MODE.split(",") if m.strip()}
    if not modes:
        modes = {"alert"}
    if "always" in modes:
        modes.update({"alert", "weekly"})
    return modes


WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def weekly_key(now_local: datetime) -> str:
    year, week_num, _ = now_local.isocalendar()
    return f"{year}-W{week_num}-{EMAIL_WEEKLY_DAY}-{EMAIL_WEEKLY_HOUR}"


def _should_send_email(
    *,
    trigger: str,
    alert_kind: str | None = None,
    now_local: datetime | None = None,
) -> tuple[bool, str]:
    """
    Decide whether we should send an email for this scan.
    Returns (should_send, reason).
    """
    if not EMAIL_ENABLED:
        return False, "EMAIL_ENABLED=0"

    modes = parse_email_modes()

    if trigger == "alert" and "alert" not in modes:
        return False, f"mode excludes alert ({EMAIL_SEND_MODE})"
    if trigger == "weekly" and "weekly" not in modes:
        return False, f"mode excludes weekly ({EMAIL_SEND_MODE})"

    if trigger == "alert" and EMAIL_ALERT_DEDUP and alert_kind is not None and alert_kind == last_alert_kind:
        return False, f"alert dedup ({alert_kind})"

    if trigger == "weekly" and EMAIL_ALERT_DEDUP and now_local is not None:
        current_weekly_key = weekly_key(now_local)
        if current_weekly_key == last_weekly_email_key:
            return False, "weekly dedup"

    if EMAIL_COOLDOWN_MINUTES > 0 and last_email_sent_at is not None:
        minutes_since = (datetime.now(timezone.utc) - last_email_sent_at).total_seconds() / 60.0
        if minutes_since < EMAIL_COOLDOWN_MINUTES:
            return False, f"cooldown {minutes_since:.1f}m/{EMAIL_COOLDOWN_MINUTES}m"

    return True, "ok"


def _smtp_config_summary() -> str:
    recipient_count = len(EMAIL_TO)
    user_hint = SMTP_USERNAME if SMTP_USERNAME else "(empty)"
    return (
        f"host={SMTP_HOST or '(empty)'} port={SMTP_PORT} "
        f"user={user_hint} from={EMAIL_FROM or '(empty)'} recipients={recipient_count}"
    )


def _resend_config_summary() -> str:
    key_hint = "(set)" if RESEND_API_KEY else "(empty)"
    recipient_count = len(EMAIL_TO)
    return (
        f"url={RESEND_API_URL} key={key_hint} from={EMAIL_FROM or '(empty)'} "
        f"recipients={recipient_count}"
    )


def _validate_smtp_config() -> tuple[bool, str]:
    missing = []
    if not EMAIL_ENABLED:
        missing.append("EMAIL_ENABLED")
    if not SMTP_HOST:
        missing.append("SMTP_HOST")
    if not EMAIL_FROM:
        missing.append("EMAIL_FROM")
    if not EMAIL_TO:
        missing.append("EMAIL_TO")
    if not SMTP_USERNAME:
        missing.append("SMTP_USERNAME")
    if not SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")

    if missing:
        return False, f"Missing/invalid env vars: {', '.join(missing)}"
    return True, "ok"


def _validate_resend_config() -> tuple[bool, str]:
    missing = []
    if not EMAIL_ENABLED:
        missing.append("EMAIL_ENABLED")
    if not RESEND_API_KEY:
        missing.append("RESEND_API_KEY")
    if not EMAIL_FROM:
        missing.append("EMAIL_FROM")
    if not EMAIL_TO:
        missing.append("EMAIL_TO")
    if missing:
        return False, f"Missing/invalid env vars: {', '.join(missing)}"
    return True, "ok"


def _send_email_smtp(subject: str, body: str, report_mode: str = "auto") -> tuple[bool, str]:
    valid, reason = _validate_smtp_config()
    if not valid:
        logger.warning(f"Email send skipped: {reason}")
        return False, reason

    try:
        text_body, html_body = build_email_bodies(subject, body, report_mode=report_mode)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = ", ".join(EMAIL_TO)
        msg.attach(MIMEText(text_body, "plain", _charset="utf-8"))
        msg.attach(MIMEText(html_body, "html", _charset="utf-8"))

        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        server.quit()
        return True, "email accepted by SMTP server"
    except Exception as e:
        logger.error(f"Failed to send email via SMTP: {e}")
        return False, str(e)


def _smtp_login_check() -> tuple[bool, str]:
    valid, reason = _validate_smtp_config()
    if not valid:
        return False, reason
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.noop()
        server.quit()
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _send_email_resend(subject: str, body: str, report_mode: str = "auto") -> tuple[bool, str]:
    valid, reason = _validate_resend_config()
    if not valid:
        logger.warning(f"Email send skipped: {reason}")
        return False, reason

    try:
        text_body, html_body = build_email_bodies(subject, body, report_mode=report_mode)
        response = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM,
                "to": EMAIL_TO,
                "subject": subject,
                "text": text_body,
                "html": html_body,
            },
            timeout=30,
        )
        if response.status_code not in (200, 201, 202):
            return False, f"Resend API {response.status_code}: {response.text[:300]}"
        return True, "email accepted by Resend API"
    except Exception as e:
        logger.error(f"Failed to send email via Resend: {e}")
        return False, str(e)


def smtp_health_check() -> tuple[bool, str]:
    if EMAIL_PROVIDER == "resend":
        valid, reason = _validate_resend_config()
        if not valid:
            return False, f"{reason}. Current: {_resend_config_summary()}"
        try:
            # Check API reachability + key validity via a lightweight domains call.
            response = httpx.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                timeout=30,
            )
            if response.status_code != 200:
                return False, f"Resend health check {response.status_code}: {response.text[:300]}"
            return True, f"Resend API reachable and key accepted. {_resend_config_summary()}"
        except Exception as e:
            return False, f"Resend health check failed: {e}. {_resend_config_summary()}"

    valid, reason = _validate_smtp_config()
    if not valid:
        return False, f"{reason}. Current: {_smtp_config_summary()}"
    ok, send_reason = _smtp_login_check()
    if ok:
        return True, f"SMTP connection/login successful. {_smtp_config_summary()}"
    return False, f"SMTP health check failed: {send_reason}. {_smtp_config_summary()}"


def send_email(subject: str, body: str, report_mode: str = "auto") -> tuple[bool, str]:
    if EMAIL_PROVIDER == "resend":
        return _send_email_resend(subject, body, report_mode=report_mode)
    return _send_email_smtp(subject, body, report_mode=report_mode)


def _extract_report_field(report: str, field_name: str) -> str:
    pattern = rf"^{re.escape(field_name)}\s*:\s*(.+)$"
    m = re.search(pattern, report, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else "N/A"


def _extract_section(report: str, start_marker: str, end_markers: list[str]) -> str:
    start_idx = report.find(start_marker)
    if start_idx == -1:
        return ""
    start_idx += len(start_marker)

    end_idx = len(report)
    for marker in end_markers:
        idx = report.find(marker, start_idx)
        if idx != -1:
            end_idx = min(end_idx, idx)
    return report[start_idx:end_idx].strip()


def _score_status(score: int) -> str:
    if score < 35:
        return "ALERT"
    if score > 70:
        return "POSITIVE"
    if score < 50:
        return "WATCH"
    return "STABLE"


def _status_color(status: str) -> str:
    if status == "ALERT":
        return "#B42318"
    if status == "POSITIVE":
        return "#027A48"
    if status == "WATCH":
        return "#B54708"
    return "#344054"


def _styled_raw_report_html(subject: str, body: str) -> str:
    escaped = html.escape(body)
    return f"""
<html>
  <body style="margin:0;padding:0;background:#f2f4f7;font-family:Arial,sans-serif;color:#101828;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #eaecf0;">
          <tr>
            <td style="background:#111827;color:#ffffff;padding:18px 24px;">
              <div style="font-size:22px;font-weight:700;">Interac Intelligence</div>
              <div style="font-size:13px;color:#d0d5dd;margin-top:4px;">{html.escape(subject)}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 24px;">
              <div style="font-size:15px;font-weight:700;margin-bottom:10px;">Report</div>
              <pre style="white-space:pre-wrap;background:#f8fafc;border:1px solid #eaecf0;border-radius:8px;padding:14px;font-size:13px;line-height:1.5;color:#101828;">{escaped}</pre>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>
""".strip()


def _build_historical_html(subject: str, body: str) -> str:
    trend = _extract_report_field(body, "OVERALL TREND")
    recent = _extract_section(
        body,
        "--- RECENT (1 month) ---",
        ["--- MEDIUM (6 months) ---", "--- OLDER (1 year+) ---", "RECURRING THEMES:", "ACTIONABLE INSIGHT:"],
    )
    medium = _extract_section(
        body,
        "--- MEDIUM (6 months) ---",
        ["--- OLDER (1 year+) ---", "RECURRING THEMES:", "ACTIONABLE INSIGHT:"],
    )
    older = _extract_section(
        body,
        "--- OLDER (1 year+) ---",
        ["RECURRING THEMES:", "ACTIONABLE INSIGHT:"],
    )
    themes = _extract_section(body, "RECURRING THEMES:", ["ACTIONABLE INSIGHT:"])
    insight = _extract_section(body, "ACTIONABLE INSIGHT:", [])

    sections = [recent, medium, older, themes, insight]
    if not any(s.strip() for s in sections):
        return _styled_raw_report_html(subject, body)

    def as_html_block(raw: str) -> str:
        if not raw:
            return "<div style='color:#667085;'>No notable findings in this timeframe.</div>"
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        items = []
        for ln in lines[:8]:
            if ln.startswith("-"):
                items.append(f"<li>{html.escape(ln[1:].strip())}</li>")
            else:
                items.append(f"<li>{html.escape(ln)}</li>")
        return f"<ul style='margin:8px 0 0 20px;padding:0;color:#101828;'>{''.join(items)}</ul>"

    return f"""
<html>
  <body style="margin:0;padding:0;background:#f2f4f7;font-family:Arial,sans-serif;color:#101828;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #eaecf0;">
          <tr>
            <td style="background:#111827;color:#ffffff;padding:18px 24px;">
              <div style="font-size:22px;font-weight:700;">Interac Intelligence</div>
              <div style="font-size:13px;color:#d0d5dd;margin-top:4px;">{html.escape(subject)}</div>
              <div style="font-size:12px;color:#98a2b3;margin-top:6px;">Historical deep scan</div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 24px;">
              <div style="border:1px solid #eaecf0;border-radius:8px;padding:12px;">
                <div style="font-size:12px;color:#667085;">Overall Trend</div>
                <div style="font-size:16px;font-weight:700;line-height:1.4;">{html.escape(trend)}</div>
              </div>
            </td>
          </tr>
          <tr><td style="padding:0 24px 20px 24px;"><hr style="border:none;border-top:1px solid #eaecf0;"></td></tr>
          <tr><td style="padding:0 24px 18px 24px;"><div style="font-size:15px;font-weight:700;">Recent (1 month)</div>{as_html_block(recent)}</td></tr>
          <tr><td style="padding:0 24px 18px 24px;"><div style="font-size:15px;font-weight:700;">Medium (6 months)</div>{as_html_block(medium)}</td></tr>
          <tr><td style="padding:0 24px 18px 24px;"><div style="font-size:15px;font-weight:700;">Older (1 year+)</div>{as_html_block(older)}</td></tr>
          <tr><td style="padding:0 24px 18px 24px;"><div style="font-size:15px;font-weight:700;">Recurring Themes</div>{as_html_block(themes)}</td></tr>
          <tr><td style="padding:0 24px 24px 24px;"><div style="font-size:15px;font-weight:700;">Actionable Insight</div>{as_html_block(insight)}</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>
""".strip()


def build_email_bodies(subject: str, body: str, report_mode: str = "auto") -> tuple[str, str]:
    resolved_mode = report_mode
    if resolved_mode == "auto":
        resolved_mode = "historical" if "OVERALL TREND:" in body else "daily"
    if resolved_mode == "historical":
        return body, _build_historical_html(subject, body)

    score_text = _extract_report_field(body, "SENTIMENT SCORE")
    volume_text = _extract_report_field(body, "MENTION VOLUME")
    ts_text = _extract_report_field(body, "TIMESTAMP")

    score_num = 50
    m = re.search(r"\b(\d{1,3})\b", score_text)
    if m:
        try:
            score_num = int(m.group(1))
        except ValueError:
            pass

    status = _score_status(score_num)
    color = _status_color(status)

    people_section = _extract_section(
        body,
        "--- WHAT PEOPLE ARE SAYING",
        ["--- PRESS & INDUSTRY ---", "PRODUCT HEALTH:", "COMPETITIVE WATCH:"],
    )
    press_section = _extract_section(
        body,
        "--- PRESS & INDUSTRY ---",
        ["PRODUCT HEALTH:", "COMPETITIVE WATCH:"],
    )
    health_section = _extract_section(
        body,
        "PRODUCT HEALTH:",
        ["COMPETITIVE WATCH:"],
    )
    comp_section = _extract_section(body, "COMPETITIVE WATCH:", [])

    def as_html_block(raw: str) -> str:
        if not raw:
            return "<div style='color:#667085;'>No material updates in this section.</div>"
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        bullets = []
        for ln in lines[:5]:
            if ln.startswith("-"):
                bullets.append(f"<li>{html.escape(ln[1:].strip())}</li>")
            else:
                bullets.append(f"<li>{html.escape(ln)}</li>")
        return f"<ul style='margin:8px 0 0 20px;padding:0;color:#101828;'>{''.join(bullets)}</ul>"

    if not any(s.strip() for s in [people_section, press_section, health_section, comp_section]):
        return body, _styled_raw_report_html(subject, body)

    html_body = f"""
<html>
  <body style="margin:0;padding:0;background:#f2f4f7;font-family:Arial,sans-serif;color:#101828;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #eaecf0;">
            <tr>
              <td style="background:#111827;color:#ffffff;padding:18px 24px;">
                <div style="font-size:22px;font-weight:700;">Interac Intelligence</div>
                <div style="font-size:13px;color:#d0d5dd;margin-top:4px;">{html.escape(subject)}</div>
                <div style="font-size:12px;color:#98a2b3;margin-top:6px;">{html.escape(ts_text)}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 24px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:33%;padding-right:8px;">
                      <div style="border:1px solid #eaecf0;border-radius:8px;padding:12px;">
                        <div style="font-size:12px;color:#667085;">Sentiment Score</div>
                        <div style="font-size:28px;font-weight:700;line-height:1.2;">{score_num}</div>
                      </div>
                    </td>
                    <td style="width:33%;padding:0 8px;">
                      <div style="border:1px solid #eaecf0;border-radius:8px;padding:12px;">
                        <div style="font-size:12px;color:#667085;">Mention Volume</div>
                        <div style="font-size:18px;font-weight:600;line-height:1.3;">{html.escape(volume_text)}</div>
                      </div>
                    </td>
                    <td style="width:33%;padding-left:8px;">
                      <div style="border:1px solid #eaecf0;border-radius:8px;padding:12px;">
                        <div style="font-size:12px;color:#667085;">Status</div>
                        <div style="display:inline-block;margin-top:6px;background:{color};color:#fff;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;">{status}</div>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 24px 20px 24px;">
                <div style="font-size:16px;font-weight:700;margin-bottom:8px;">Executive Summary</div>
                <ul style="margin:0 0 0 20px;padding:0;color:#101828;">
                  <li>Current sentiment status is <b>{status}</b> with score <b>{score_num}</b>.</li>
                  <li>Mention volume appears <b>{html.escape(volume_text)}</b> for this cycle.</li>
                  <li>Use sections below to review user signals, press updates, and product health.</li>
                </ul>
              </td>
            </tr>
            <tr><td style="padding:0 24px 20px 24px;"><hr style="border:none;border-top:1px solid #eaecf0;"></td></tr>
            <tr>
              <td style="padding:0 24px 20px 24px;">
                <div style="font-size:15px;font-weight:700;">What People Are Saying</div>
                {as_html_block(people_section)}
              </td>
            </tr>
            <tr>
              <td style="padding:0 24px 20px 24px;">
                <div style="font-size:15px;font-weight:700;">Press & Industry</div>
                {as_html_block(press_section)}
              </td>
            </tr>
            <tr>
              <td style="padding:0 24px 20px 24px;">
                <div style="font-size:15px;font-weight:700;">Product Health</div>
                {as_html_block(health_section)}
              </td>
            </tr>
            <tr>
              <td style="padding:0 24px 24px 24px;">
                <div style="font-size:15px;font-weight:700;">Competitive Watch</div>
                {as_html_block(comp_section)}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    return body, html_body


def _record_email_sent(trigger: str, *, alert_kind: str | None = None, now_local: datetime | None = None) -> None:
    global last_email_sent_at, last_alert_kind, last_weekly_email_key
    last_email_sent_at = datetime.now(timezone.utc)
    if trigger == "alert":
        last_alert_kind = alert_kind
    elif trigger == "weekly" and now_local is not None:
        last_weekly_email_key = weekly_key(now_local)


def weekly_est_to_utc(day_name: str, hour_est: int) -> tuple[int, int]:
    base_day = WEEKDAY_TO_INDEX.get(day_name, 0)
    hour_utc = hour_est + 5  # EST -> UTC
    day_shift = 0
    if hour_utc >= 24:
        hour_utc -= 24
        day_shift = 1
    return (base_day + day_shift) % 7, hour_utc


async def ask_followup(question: str, report_context: str) -> str:
    config = load_prompts()
    return await call_kimi(
        config["followup_prompt"],
        f"Latest report:\n{report_context}\n\nRaw mentions:\n{last_mentions_raw[:3000]}\n\nQuestion: {question}",
    )


# ─── Scheduled Broadcast ─────────────────────────────────────────────────────
async def scheduled_sentiment_broadcast(context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw, last_sentiment_score, last_alert_kind
    logger.info(f"[{now_est()}] Running scheduled Interac sentiment scan...")

    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report

        score = extract_sentiment_score(report)
        config = load_prompts()
        threshold = config.get("alert_threshold", 35)

        # Check for low/high alert conditions.
        alert_prefix = ""
        is_low_alert = score < threshold
        is_high_alert = score > ALERT_HIGH_THRESHOLD
        alert_kind = None
        if is_low_alert:
            alert_kind = "low"
            alert_prefix = f"🚨 *ALERT: Sentiment dropped to {score}/100* 🚨\n\n"
        elif is_high_alert:
            alert_kind = "high"
            alert_prefix = f"🔥 *SPIKE: Sentiment jumped to {score}/100* 🔥\n\n"

        last_sentiment_score = score
        message = f"{alert_prefix}📊 *Interac Intelligence* — {now_est()}\n\n{report}"

        if alert_kind is not None:
            should_send, reason = _should_send_email(trigger="alert", alert_kind=alert_kind)
        else:
            should_send, reason = (False, "no alert condition")

        if should_send:
            label = "ALERT" if alert_kind == "low" else "SPIKE"
            subject = f"{EMAIL_SUBJECT_PREFIX} — {label} ({score}/100)"
            body_lines = [f"Interac Intelligence — {now_est()}", ""]
            if alert_kind == "low":
                body_lines += [f"ALERT: Sentiment dropped to {score}/100 (threshold {threshold})", ""]
            elif alert_kind == "high":
                body_lines += [f"SPIKE: Sentiment rose to {score}/100 (high threshold {ALERT_HIGH_THRESHOLD})", ""]
            body_lines.append(report)
            ok, send_reason = send_email(subject=subject, body="\n".join(body_lines), report_mode="daily")
            if ok:
                _record_email_sent("alert", alert_kind=alert_kind)
            else:
                logger.error(f"Alert email failed: {send_reason}")
        else:
            logger.info(f"Email not sent: {reason}")

        last_alert_kind = alert_kind

        for chat_id in subscribed_chats.copy():
            try:
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")
                subscribed_chats.discard(chat_id)
    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


async def scheduled_weekly_email_digest(context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw, last_sentiment_score
    now_local = datetime.now(EST)
    target_weekday = WEEKDAY_TO_INDEX.get(EMAIL_WEEKLY_DAY, 0)
    if now_local.weekday() != target_weekday or now_local.hour != EMAIL_WEEKLY_HOUR:
        return

    should_send, reason = _should_send_email(trigger="weekly", now_local=now_local)
    if not should_send:
        logger.info(f"Weekly email not sent: {reason}")
        return

    logger.info(f"[{now_est()}] Running weekly email digest scan...")
    try:
        mentions = await fetch_all_mentions()
        last_mentions_raw = mentions
        report = await analyze_sentiment(mentions)
        last_report = report
        score = extract_sentiment_score(report)
        last_sentiment_score = score

        subject = f"{EMAIL_SUBJECT_PREFIX} — WEEKLY DIGEST ({score}/100)"
        body = f"Interac Intelligence Weekly Digest — {now_est()}\n\n{report}"
        ok, send_reason = send_email(subject, body, report_mode="daily")
        if ok:
            _record_email_sent("weekly", now_local=now_local)
        else:
            logger.error(f"Weekly email failed: {send_reason}")
    except Exception as e:
        logger.error(f"Weekly email digest failed: {e}")


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
        "• /email — Admin: run scan + send email now\n"
        "• /deepscan — Admin: historical scan + email\n"
        "• /smtpcheck — Admin: check SMTP config/login\n"
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
        f"Edit `prompts.json` for config and `prompts/*.md` for prompts.",
        parse_mode="Markdown",
    )


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_report, last_mentions_raw, last_sentiment_score
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text("📧 Running fresh scan and sending email...")
    try:
        # Hard timeout so manual email runs never hang indefinitely.
        mentions = await asyncio.wait_for(fetch_all_mentions(), timeout=60)
        last_mentions_raw = mentions
        report = await asyncio.wait_for(analyze_sentiment(mentions), timeout=60)
        last_report = report

        score = extract_sentiment_score(report)
        last_sentiment_score = score
        config = load_prompts()
        low_threshold = config.get("alert_threshold", 35)

        subject = f"{EMAIL_SUBJECT_PREFIX} — MANUAL REPORT ({score}/100)"
        body = (
            f"Interac Intelligence — {now_est()}\n\n"
            f"Manual /email trigger\n"
            f"Low alert threshold: {low_threshold}\n"
            f"High alert threshold: {ALERT_HIGH_THRESHOLD}\n\n"
            f"{report}"
        )
        ok, send_reason = send_email(subject, body, report_mode="daily")
        if ok:
            _record_email_sent("on_demand")
            await update.message.reply_text("✅ Email sent successfully.")
        else:
            await update.message.reply_text(f"❌ Email failed: {send_reason}")
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ /email timed out after 60 seconds.")
    except Exception as e:
        logger.error(f"/email failed: {e}")
        await update.message.reply_text(f"❌ /email failed: {e}")


async def cmd_deepscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text("🧠 Running historical deep scan...")
    try:
        historical_mentions = await asyncio.wait_for(fetch_historical_mentions(), timeout=120)
        report = await asyncio.wait_for(analyze_historical(historical_mentions), timeout=120)

        telegram_message = f"📚 *Interac Historical Deep Scan* — {now_est()}\n\n{report}"
        await update.message.reply_text(telegram_message, parse_mode="Markdown")

        subject = f"{EMAIL_SUBJECT_PREFIX} — HISTORICAL DEEP SCAN"
        email_body = f"Interac Historical Deep Scan — {now_est()}\n\n{report}"
        ok, send_reason = send_email(subject, email_body, report_mode="historical")
        if ok:
            _record_email_sent("on_demand")
            await update.message.reply_text("✅ Deep scan email sent successfully.")
        else:
            await update.message.reply_text(f"❌ Deep scan email failed: {send_reason}")
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ /deepscan timed out after 120 seconds.")
    except Exception as e:
        logger.error(f"/deepscan failed: {e}")
        await update.message.reply_text(f"❌ /deepscan failed: {e}")


async def cmd_smtpcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only.")
        return

    ok, reason = smtp_health_check()
    if ok:
        await update.message.reply_text(f"✅ {reason}")
    else:
        await update.message.reply_text(f"❌ {reason}")


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
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("deepscan", cmd_deepscan))
    app.add_handler(CommandHandler("smtpcheck", cmd_smtpcheck))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 6am, 10am, 2pm, 6pm EST = 11, 15, 19, 23 UTC
    job_queue = app.job_queue
    for utc_hour in [11, 15, 19, 23]:
        job_queue.run_daily(
            scheduled_sentiment_broadcast,
            time=datetime.strptime(f"{utc_hour:02d}:00", "%H:%M").time(),
            name=f"sentiment_{utc_hour:02d}",
        )

    _, weekly_hour_utc = weekly_est_to_utc(EMAIL_WEEKLY_DAY, EMAIL_WEEKLY_HOUR)
    # Compatibility fallback: some python-telegram-bot JobQueue builds do not expose run_weekly.
    # Run daily at the target hour and guard weekday/hour inside the callback.
    job_queue.run_daily(
        scheduled_weekly_email_digest,
        time=datetime.strptime(f"{weekly_hour_utc:02d}:00", "%H:%M").time(),
        name="weekly_email_digest",
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

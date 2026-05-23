"""
Telegram alert delivery via direct Bot API HTTP calls.
Uses only the `requests` library (already a core dependency) — no
python-telegram-bot package needed. This avoids version churn and works
with any future Telegram Bot API version.

API docs: https://core.telegram.org/bots/api#sendmessage
"""

import logging
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_BASE = "https://api.telegram.org/bot{token}/{method}"
_bot_available: Optional[bool] = None


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_telegram_available() -> bool:
    """Return True if credentials are configured and the bot is reachable."""
    global _bot_available
    if _bot_available is not None:
        return _bot_available
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        _bot_available = False
        logger.warning("Telegram credentials not configured in .env")
        return False
    try:
        resp = requests.get(
            _TELEGRAM_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method="getMe"),
            timeout=10,
        )
        _bot_available = resp.status_code == 200 and resp.json().get("ok", False)
    except Exception as exc:
        logger.warning("Telegram availability check failed: %s", exc)
        _bot_available = False
    return _bot_available


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------

def send_message(text: str) -> bool:
    """
    Send a plain-text message to the configured Telegram chat.
    Long messages are automatically split into ≤4000-char chunks.
    Returns True on success, False on any failure.
    One failed ticker or API hiccup never raises — always returns a bool.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — message not sent")
        return False

    url = _TELEGRAM_BASE.format(
        token=settings.TELEGRAM_BOT_TOKEN,
        method="sendMessage",
    )

    chunks = _split_message(text)
    success = True
    for chunk in chunks:
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "text": chunk,
                },
                timeout=15,
            )
            if not resp.ok:
                logger.error(
                    "Telegram API error %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                success = False
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            success = False

    if success:
        logger.info(
            "Telegram: sent %d chunk(s) to chat %s",
            len(chunks),
            settings.TELEGRAM_CHAT_ID,
        )
    return success


# ---------------------------------------------------------------------------
# Convenience senders
# ---------------------------------------------------------------------------

def send_startup_message() -> bool:
    """Send a startup confirmation message."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return send_message(
        f"✅ SEPA System Started\n"
        f"Time: {ts} BST\n"
        f"Scanning: S&P 500 + Russell 1000\n"
        f"Dashboard: http://localhost:{settings.DASHBOARD_PORT}\n"
        f"Version: 3.0"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_message(text: str, max_length: int = 4000) -> list[str]:
    """Split long messages at newline boundaries to stay under Telegram's 4096-char limit."""
    if len(text) <= max_length:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        cut = text[:max_length].rfind("\n")
        if cut == -1:
            cut = max_length
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    available = is_telegram_available()
    print(f"telegram_bot.py: Telegram available = {available}")
    if available:
        ok = send_message("🧪 Test message from SEPA system — ignore")
        print(f"  Send test: {'OK' if ok else 'FAILED'}")

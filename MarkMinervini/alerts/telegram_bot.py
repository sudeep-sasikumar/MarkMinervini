"""
Telegram alert delivery (Section 11).
Uses python-telegram-bot library (async send via Bot.send_message).
All formatting is handled by alert_formatter.py — this module only sends.
"""

import asyncio
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

_bot = None
_bot_available: Optional[bool] = None


def _get_bot():
    """Lazy-initialise the Telegram Bot instance."""
    global _bot
    if _bot is None:
        if not settings.TELEGRAM_BOT_TOKEN:
            return None
        try:
            from telegram import Bot
            _bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        except Exception as exc:
            logger.warning("Telegram bot init failed: %s", exc)
    return _bot


def is_telegram_available() -> bool:
    """Check if Telegram credentials are configured and the bot is reachable."""
    global _bot_available
    if _bot_available is not None:
        return _bot_available
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        _bot_available = False
        return False
    try:
        bot = _get_bot()
        if bot is None:
            _bot_available = False
            return False
        asyncio.get_event_loop().run_until_complete(bot.get_me())
        _bot_available = True
    except Exception as exc:
        logger.warning("Telegram not reachable: %s", exc)
        _bot_available = False
    return _bot_available


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message to the configured Telegram chat.
    Returns True on success, False on failure.
    Telegram message limit is 4096 chars; longer messages are split automatically.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured — message not sent")
        return False

    bot = _get_bot()
    if bot is None:
        return False

    try:
        loop = _get_or_create_loop()
        # Split long messages
        chunks = _split_message(text)
        for chunk in chunks:
            loop.run_until_complete(
                bot.send_message(
                    chat_id=settings.TELEGRAM_CHAT_ID,
                    text=chunk,
                    parse_mode=None,  # plain text for reliability with special chars
                )
            )
        logger.info("Telegram: sent %d chunk(s) to chat %s", len(chunks), settings.TELEGRAM_CHAT_ID)
        return True
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_startup_message() -> bool:
    """Send a startup confirmation message."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"✅ SEPA System Started\n"
        f"Time: {ts}\n"
        f"Scanning: S&P 500 + Russell 1000\n"
        f"Dashboard: http://localhost:{settings.DASHBOARD_PORT}\n"
        f"Version: 3.0"
    )
    return send_message(msg)


def _split_message(text: str, max_length: int = 4000) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Find last newline before limit
        cut = text[:max_length].rfind("\n")
        if cut == -1:
            cut = max_length
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    available = is_telegram_available()
    print(f"telegram_bot.py: Telegram available = {available}")
    if available:
        ok = send_message("🧪 Test message from SEPA system — ignore")
        print(f"  Send test: {'OK' if ok else 'FAILED'}")

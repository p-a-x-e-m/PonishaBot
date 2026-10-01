"""Send alerts to Telegram via the Bot API."""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def _esc(text: str) -> str:
    """Escape HTML special chars — titles from ponisha may contain & or <."""
    return (str(text).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send(self, text: str, disable_preview: bool = True, context: str = "") -> bool:
        """Send a text message. Always logs success or why it was not sent."""
        label = context or "message"
        if not self.enabled:
            logger.info(
                f"Telegram notification NOT sent ({label}): "
                f"bot_token/chat_id not configured (notifier disabled)"
            )
            return False
        try:
            resp = requests.post(
                API_BASE.format(token=self.bot_token),
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": disable_preview,
                },
                timeout=15,
            )
            resp.raise_for_status()
            logger.info(f"Telegram notification sent: {label}")
            return True
        except Exception as e:
            logger.error(f"Telegram notification failed ({label}): {e}")
            return False

    def notify_startup(self, mode: str) -> None:
        self.send(f"🤖 ponishabot started (mode: {mode})", context=f"startup ({mode})")

    def notify_new_project(self, title: str, url: str, budget: str, skills: list,
                           price: Optional[int] = None,
                           duration_days: Optional[int] = None,
                           reason: str = "",
                           market_avg: Optional[int] = None) -> bool:
        """Return True if Telegram accepted the message (False if disabled/failed)."""
        skills_str = ", ".join(_esc(s) for s in skills) if skills else "—"
        lines = [
            "🆕 New matching project:",
            f"📌 {_esc(title)}",
            f"💰 Budget: {_esc(budget)}",
        ]
        if market_avg is not None:
            lines.append(f"📊 Market avg (karlancer): {market_avg:,} Toman")
        if price:
            days_str = f"{duration_days} days" if duration_days else "—"
            lines.append(f"🎯 Suggested: {price:,} Toman | ⏱ {days_str}")
        if reason:
            lines.append(f"💬 {_esc(reason[:200])}")
        lines += [f"🛠 Skills: {skills_str}", f"🔗 {url}"]
        return self.send("\n".join(lines), context=f"match: {title[:60]}")

    def notify_bid(self, title: str, price: Optional[int], days: Optional[int]) -> None:
        price_str = f"{price:,} Toman" if price else "—"
        days_str = f"{days} days" if days else "—"
        self.send(
            f"✅ Bid placed:\n"
            f"📌 {_esc(title)}\n"
            f"💰 Price: {price_str} | ⏱ Duration: {days_str}"
        )

    def notify_quota_low(self, remaining: int) -> None:
        self.send(
            f"⚠️ Proposal quota low: only {remaining} left!\n"
            f"The bot has stopped bidding. Top up your ponisha plan, then "
            f"press Start again."
        )

    def notify_error(self, msg: str) -> None:
        self.send(f"⚠️ Error in ponishabot:\n{_esc(msg[:500])}")

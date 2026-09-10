from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import aiohttp

if TYPE_CHECKING:
    from src.core.client import Twitch

logger = logging.getLogger("TwitchDrops.telegram")


class TelegramService:
    """Сервис для отправки уведомлений в Telegram."""

    def __init__(self, twitch: Twitch):
        self._twitch = twitch

    @property
    def config(self) -> dict:
        return getattr(self._twitch.settings, "telegram", {})

    @property
    def enabled(self) -> bool:
        cfg = self.config
        return bool(cfg.get("enabled", False) and cfg.get("token") and cfg.get("chat_id"))

    async def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False

        token = str(self.config.get("token", "")).strip()
        chat_id = str(self.config.get("chat_id", "")).strip()
        if not token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            session = await self._twitch.get_session()
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return True
                err = await resp.text()
                logger.warning(f"Telegram API error ({resp.status}): {err}")
                return False
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")
            return False
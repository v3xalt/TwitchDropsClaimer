from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
import aiohttp

if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.models.drop import BaseDrop

logger = logging.getLogger("TwitchDrops.telegram")


class TelegramService:
    """Сервис для отправки форматированных уведомлений и фото в Telegram."""

    def __init__(self, twitch: Twitch):
        self._twitch = twitch

    @property
    def config(self) -> dict:
        return getattr(self._twitch.settings, "telegram", {})

    @property
    def enabled(self) -> bool:
        cfg = self.config
        return bool(cfg.get("enabled", False) and cfg.get("token") and cfg.get("chat_id"))

    @property
    def _api_url(self) -> str:
        token = str(self.config.get("token", "")).strip()
        return f"https://api.telegram.org/bot{token}"

    @property
    def _chat_id(self) -> str:
        return str(self.config.get("chat_id", "")).strip()

    async def send_message(self, text: str, reply_markup: dict[str, Any] | None = None) -> bool:
        """Отправка обычного текстового сообщения."""
        if not self.enabled:
            return False

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            session = await self._twitch.get_session()
            async with session.post(
                f"{self._api_url}/sendMessage",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning(f"Telegram sendMessage failed: {e}")
            return False

    async def send_photo(
        self, photo_url: str, caption: str, reply_markup: dict[str, Any] | None = None
    ) -> bool:
        """Отправка изображения с подписью (с фолбэком на текст при ошибке)."""
        if not self.enabled:
            return False

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            session = await self._twitch.get_session()
            async with session.post(
                f"{self._api_url}/sendPhoto",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return True
                logger.warning(f"Telegram sendPhoto failed ({resp.status}), fallback to sendMessage")
        except Exception as e:
            logger.warning(f"Telegram sendPhoto exception: {e}, fallback to sendMessage")

        # Если Telegram не смог загрузить картинку с CDN Twitch, отправляем просто красиво оформленный текст
        return await self.send_message(caption, reply_markup=reply_markup)

    async def notify_drop_claimed(self, drop: BaseDrop) -> bool:
        """Формирует и отправляет красивую карточку полученного дропа."""
        if not self.enabled:
            return False

        game_name = drop.campaign.game.name
        campaign_name = drop.campaign.name
        claimed = drop.campaign.claimed_drops
        total = drop.campaign.total_drops

        # 1. Текстовый прогресс-бар: [██████░░░░] 60% (3 / 5)
        if total > 0:
            filled = min(10, max(0, int(round(10 * claimed / total))))
            bar = "█" * filled + "░" * (10 - filled)
            percent = int(round(claimed / total * 100))
            progress_bar = f"<code>[{bar}]</code> <b>{percent}%</b> ({claimed}/{total})"
        else:
            progress_bar = f"{claimed}/{total}"

        # 2. Список наград
        benefit_lines = [f"• <b>{b.name}</b>" for b in drop.benefits if b.name]
        if benefit_lines:
            rewards_text = "\n".join(benefit_lines)
        else:
            rewards_text = f"• <b>{drop.name}</b>"

        # 3. Инфо о текущем канале
        channel = self._twitch.watching_channel.get_with_default(None)
        channel_text = f"\n📺 <b>Канал:</b> {channel.name}" if channel else ""

        # 4. Проверка завершения всей кампании
        is_finished = total > 0 and claimed >= total
        finish_banner = "\n\n🏆 <b>Кампания завершена! Все награды получены!</b>" if is_finished else ""

        # Собираем красивую подпись к сообщению
        caption = (
            f"🎁 <b>Награда получена!</b>\n\n"
            f"🎮 <b>Игра:</b> {game_name}\n"
            f"📦 <b>Кампания:</b> {campaign_name}\n"
            f"✨ <b>Награда:</b>\n{rewards_text}\n\n"
            f"📊 <b>Прогресс кампании:</b> {progress_bar}"
            f"{channel_text}"
            f"{finish_banner}"
        )

        # 5. Кнопки со ссылками
        buttons = [
            {"text": "🎁 Инвентарь Twitch", "url": "https://www.twitch.tv/drops/inventory"}
        ]
        if getattr(drop.campaign, "campaign_url", None):
            buttons.append({"text": "📋 О кампании", "url": drop.campaign.campaign_url})

        reply_markup = {"inline_keyboard": [buttons]}

        # 6. Поиск картинки (сначала картинка скина, затем обложка игры)
        image_url = None
        for benefit in drop.benefits:
            if getattr(benefit, "image_url", None):
                image_url = str(benefit.image_url)
                break

        if not image_url and getattr(drop.campaign.game, "box_art_url", None):
            image_url = drop.campaign.game.box_art_url.replace("{width}", "285").replace("{height}", "380")

        if image_url:
            return await self.send_photo(image_url, caption=caption, reply_markup=reply_markup)
        else:
            return await self.send_message(caption, reply_markup=reply_markup)

    async def send_test_drop(self) -> bool:
        """Отправляет тестовую карточку дропа (берет реальный дроп из инвентаря или генерирует демо)."""
        if not self.enabled:
            return False

        # 1. Если уже загружены реальные кампании, берем первый доступный дроп
        for campaign in self._twitch.inventory:
            for drop in campaign.drops:
                return await self.notify_drop_claimed(drop)

        # 2. Если кампании еще не загрузились, отправляем красивое демо с картинкой
        demo_caption = (
            "🎁 <b>Награда получена! (ТЕСТ)</b>\n\n"
            "🎮 <b>Игра:</b> WARDOGS\n"
            "📦 <b>Кампания:</b> WARDOGS Launch Drops\n"
            "✨ <b>Награда:</b>\n"
            "• <b>Silver Drop 1 [EA 0.1]</b>\n"
            "• <b>Exclusive Weapon Camo</b>\n\n"
            "📊 <b>Прогресс кампании:</b> <code>[██████░░░░]</code> <b>60%</b> (3/5)\n"
            "📺 <b>Канал:</b> TheBurntPeanut\n\n"
            "<i>(Это тестовое уведомление для проверки отображения фото и кнопок)</i>"
        )
        buttons = [
            {"text": "🎁 Инвентарь Twitch", "url": "https://www.twitch.tv/drops/inventory"},
            {"text": "📋 О кампании", "url": "https://www.twitch.tv/drops/campaigns"},
        ]
        reply_markup = {"inline_keyboard": [buttons]}

        # Официальная иконка Twitch Drops
        test_image = "https://static-cdn.jtvnw.net/c3-vg/drop-events/5a4da2ab-3d5b-47c9-f9ce-864e727b2cb3/d28e549b-b025-bd0f-7519-30a455deea71.png"
        return await self.send_photo(test_image, caption=demo_caption, reply_markup=reply_markup)
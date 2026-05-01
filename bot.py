"""
Бот оповещений для Севастополя — БЕЗ Telethon и API ключей.
Читает публичные Telegram-каналы через t.me/s/канал (веб-версия).
Нужен только Bot Token от @BotFather.
"""

import os
import asyncio
import logging
import re
import html
import aiohttp
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))  # Москва / Севастополь UTC+3

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.getenv("BOT_TOKEN",  "ВАШ_BOT_TOKEN")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "raidalert")

CHAT_IDS = [
    int(i.strip())
    for i in os.getenv("MY_CHAT_IDS", os.getenv("MY_CHAT_ID", "0")).split(",")
    if i.strip().lstrip("-").isdigit()
]

CHANNELS = [
    c.strip()
    for c in os.getenv("CHANNELS", "sevdortrans_ru,alertsev,raid_test").split(",")
]

# ═══════════════════════════════════════════════════════════════════
#  КЛЮЧЕВЫЕ СЛОВА
# ═══════════════════════════════════════════════════════════════════

RAID_CLOSED = [
    r"закрыти[ием]+\s*рейда",
    r"рейд\s*(закрыт|прекращ|остановлен|закрыли)",
    r"приостановил\s*движение",
    r"морской\s*пассажирск\w+\s*транспорт\s*приостановил",
    r"катер[аы]?\s*(не\s*ход|закрыт|остановлен|отменен|не\s*работ)",
    r"переправ[аы]?\s*(закрыт|остановлен|не\s*работает)",
    r"движение.*остановлен",
    r"рейд.*закрыт",
    r"закрыт.*рейд",
    r"компенсационн\w+\s*маршрут",
]

RAID_OPEN = [
    r"рейд\s*(открыт|возобновлен|работает)",
    r"катер[аы]?\s*(ход[яу]т|открыт|работа)",
    r"переправ[аы]?\s*(открыт|возобновлен|работает)",
    r"движение\s*(возобновлено|восстановлено|возобновляет)",
    r"морской\s*пассажирск\w+\s*транспорт\s*возобновл",
    r"рейд.*открыт",
    r"открыт.*рейд",
    r"катера\s*работают",
]

ALERT = [
    r"воздушн\w*\s*тревог",
    r"тревог[аи]\s*(объявлена|введена)",
    r"\bбпла\b",
    r"дрон[ыа]?\s*(замечен|зафиксирован|летит|атак)",
    r"ракет\w+\s*(опасност|угроз|атак|удар)",
    r"отбой\s*тревог",
    r"угроза\s*(удара|атаки)",
    r"опасность\s*по\s*бпла",
]

IGNORE = ["реклама", "акция", "скидка", "конкурс", "розыгрыш", "подписывайтесь"]

# ═══════════════════════════════════════════════════════════════════
#  ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

def classify(text: str) -> str | None:
    t = text.lower()
    if any(w in t for w in IGNORE):
        return None
    for p in RAID_CLOSED:
        if re.search(p, t):
            return "raid_closed"
    for p in RAID_OPEN:
        if re.search(p, t):
            return "raid_open"
    for p in ALERT:
        if re.search(p, t):
            return "alert"
    return None


def build_notification(msg_type: str, channel: str, text: str) -> str:
    time_str = datetime.now(MSK).strftime("%H:%M")
    preview = html.escape(text[:350].strip())
    if len(text) > 350:
        preview += "..."
    icons = {
        "raid_closed": ("🚫", "РЕЙД ЗАКРЫТ",    "⛵ Катера не ходят"),
        "raid_open":   ("✅", "РЕЙД ОТКРЫТ",    "⛵ Катера работают"),
        "alert":       ("🚨", "ТРЕВОГА / БПЛА", "🛡️ Будьте осторожны"),
    }
    emoji, title, footer = icons[msg_type]
    return (
        f"{emoji} <b>{title}</b>\n\n"
        f"📢 Канал: @{channel}\n"
        f"🕐 {time_str}\n\n"
        f"<blockquote>{preview}</blockquote>\n\n"
        f"{footer}"
    )


async def fetch_channel_posts(session: aiohttp.ClientSession, channel: str) -> list[dict]:
    """Получить посты за последние 7 минут из публичного канала."""
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AlertBot/1.0)"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Канал {channel}: HTTP {resp.status}")
                return []
            page = await resp.text()

        posts = []
        now = datetime.now(timezone.utc)

        blocks = re.findall(
            r'data-post="([^"]+)"(.*?)</div>\s*</div>\s*</div>',
            page, re.DOTALL
        )

        for post_id, block in blocks:
            t_match = re.search(r'datetime="([^"]+)"', block)
            if not t_match:
                continue
            try:
                post_time = datetime.fromisoformat(t_match.group(1).replace("Z", "+00:00"))
                if (now - post_time).total_seconds() > 7 * 60:
                    continue
            except Exception:
                continue

            tx_match = re.search(
                r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                block, re.DOTALL
            )
            if not tx_match:
                continue

            clean = re.sub(r'<[^>]+>', ' ', tx_match.group(1))
            clean = html.unescape(re.sub(r'\s+', ' ', clean).strip())
            if clean:
                posts.append({"id": post_id, "text": clean})

        logger.info(f"Канал @{channel}: найдено {len(posts)} свежих постов")
        return posts

    except Exception as e:
        logger.error(f"Ошибка чтения @{channel}: {e}")
        return []


async def send_telegram(session: aiohttp.ClientSession, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"Telegram error (chat {chat_id}): {body}")


async def send_ntfy(session: aiohttp.ClientSession, title: str, message: str, priority: str = "high"):
    clean = html.unescape(re.sub(r"<[^>]+>", "", message)).strip()
    try:
        async with session.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=clean.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "loudspeaker"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                logger.error(f"ntfy error: {resp.status}")
    except Exception as e:
        logger.error(f"ntfy send error: {e}")


async def check_all_channels(session: aiohttp.ClientSession):
    for channel in CHANNELS:
        posts = await fetch_channel_posts(session, channel)

        for post in posts:
            msg_type = classify(post["text"])
            logger.info(f"Пост @{channel}: '{post['text'][:60]}' -> {msg_type}")

            # raid_test — тестовый канал, принимаем любой тип
            if channel != "raid_test":
                if msg_type in ("raid_closed", "raid_open") and channel != "sevdortrans_ru":
                    msg_type = None
                if msg_type == "alert" and channel != "alertsev":
                    msg_type = None

            if msg_type:
                notification = build_notification(msg_type, channel, post["text"])
                await send_telegram(session, notification)

                ntfy_titles = {
                    "raid_closed": "🚫 РЕЙД ЗАКРЫТ",
                    "raid_open":   "✅ РЕЙД ОТКРЫТ",
                    "alert":       "🚨 ТРЕВОГА / БПЛА",
                }
                await send_ntfy(session, ntfy_titles[msg_type], post["text"][:200],
                                "urgent" if msg_type == "alert" else "high")

                logger.info(f"✅ Отправлено [{msg_type}] из @{channel}")


async def main():
    logger.info("Запуск бота (GitHub Actions режим)...")
    logger.info(f"Каналы: {CHANNELS}")
    async with aiohttp.ClientSession() as session:
        try:
            await check_all_channels(session)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
    logger.info("Проверка завершена.")


if __name__ == "__main__":
    asyncio.run(main())

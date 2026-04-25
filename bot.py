"""
Бот оповещений для Севастополя — БЕЗ Telethon и API ключей.
Читает публичные Telegram-каналы через t.me/s/канал (веб-версия).
Нужен только Bot Token от @BotFather.
"""

import os
import asyncio
import logging
import re
import json
import aiohttp
from datetime import datetime

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════════

# Токен от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_BOT_TOKEN")

# Ваш chat_id (узнать: написать /start боту @userinfobot)
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

# Публичные каналы для мониторинга (username без @)
# Найдите нужные севастопольские каналы и вставьте сюда
CHANNELS = [
    c.strip()
    for c in os.getenv(
        "CHANNELS",
        "sevdortrans_ru,alertsev"
    ).split(",")
]

# Интервал проверки (секунды). 60 = раз в минуту
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

# ═══════════════════════════════════════════════════════════════════
#  КЛЮЧЕВЫЕ СЛОВА
# ═══════════════════════════════════════════════════════════════════

RAID_CLOSED = [
    # Прямые фразы из реальных сообщений канала "Транспорт Севастополя"
    r"закрыти[ием]+\s*рейда",                        # "закрытием рейда"
    r"рейд\s*(закрыт|прекращ|остановлен|закрыли)",
    r"приостановил\s*движение",                       # "приостановил движение"
    r"морской\s*пассажирск\w+\s*транспорт\s*приостановил",
    r"катер[аы]?\s*(не\s*ход|закрыт|остановлен|отменен|не\s*работ)",
    r"переправ[аы]?\s*(закрыт|остановлен|не\s*работает)",
    r"движение.*остановлен",
    r"рейд.*закрыт",
    r"закрыт.*рейд",
    r"компенсационн\w+\s*маршрут",                   # автобусы вместо катеров
]

RAID_OPEN = [
    r"рейд\s*(открыт|возобновлен|работает)",
    r"катер[аы]?\s*(ход[яу]т|открыт|работа)",
    r"переправ[аы]?\s*(открыт|возобновлен|работает)",
    r"движение\s*(возобновлено|восстановлено)",
    r"морской\s*пассажирск\w+\s*транспорт\s*возобновил",
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
]

IGNORE = ["реклама", "акция", "скидка", "конкурс", "розыгрыш", "подписывайтесь"]

# ═══════════════════════════════════════════════════════════════════
#  ХРАНИЛИЩЕ (запоминаем какие посты уже видели)
# ═══════════════════════════════════════════════════════════════════

seen_file = "seen_posts.json"

def load_seen() -> dict:
    if os.path.exists(seen_file):
        with open(seen_file) as f:
            return json.load(f)
    return {}

def save_seen(data: dict):
    with open(seen_file, "w") as f:
        json.dump(data, f)

seen_posts: dict = load_seen()

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
    time_str = datetime.now().strftime("%H:%M")
    preview = text[:350].strip()
    if len(text) > 350:
        preview += "..."

    icons = {
        "raid_closed": ("🚫", "РЕЙД ЗАКРЫТ", "⛵ Катера не ходят"),
        "raid_open":   ("✅", "РЕЙД ОТКРЫТ", "⛵ Катера работают"),
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
    """Получить последние посты из публичного канала через веб."""
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AlertBot/1.0)"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Канал {channel}: HTTP {resp.status}")
                return []
            html = await resp.text()

            # Извлекаем ID постов и текст
            posts = []
            # Ищем блоки сообщений
            message_blocks = re.findall(
                r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL
            )
            for post_id, raw_text in message_blocks:
                # Чистим HTML теги
                clean = re.sub(r'<[^>]+>', ' ', raw_text)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if clean:
                    posts.append({"id": post_id, "text": clean})

            return posts[-20:]  # последние 20 постов
    except Exception as e:
        logger.error(f"Ошибка чтения {channel}: {e}")
        return []


async def send_telegram(session: aiohttp.ClientSession, text: str):
    """Отправить сообщение через Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            logger.error(f"Telegram API error: {body}")


async def check_all_channels(session: aiohttp.ClientSession):
    """Проверить все каналы на новые релевантные посты."""
    for channel in CHANNELS:
        posts = await fetch_channel_posts(session, channel)

        channel_seen = seen_posts.get(channel, [])

        for post in posts:
            post_id = post["id"]
            if post_id in channel_seen:
                continue  # уже видели

            # Новый пост — классифицируем
            msg_type = classify(post["text"])
            if msg_type:
                notification = build_notification(msg_type, channel, post["text"])
                await send_telegram(session, notification)
                logger.info(f"[{msg_type}] из @{channel}: {post['text'][:60]}...")

            # Запоминаем пост
            channel_seen.append(post_id)

        # Храним только последние 100 ID
        seen_posts[channel] = channel_seen[-100:]

    save_seen(seen_posts)


async def main():
    logger.info("Запуск бота...")
    logger.info(f"Каналы: {CHANNELS}")
    logger.info(f"Интервал: {CHECK_INTERVAL} сек.")

    async with aiohttp.ClientSession() as session:
        # Стартовое уведомление
        await send_telegram(session,
            "✅ <b>Бот запущен!</b>\n\n"
            f"📡 Каналов: {len(CHANNELS)}\n"
            f"🔄 Проверка каждые {CHECK_INTERVAL} сек.\n"
            "🔍 Слежу за: рейд, катера, БПЛА, тревога"
        )

        while True:
            try:
                await check_all_channels(session)
            except Exception as e:
                logger.error(f"Ошибка цикла: {e}")
            await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())

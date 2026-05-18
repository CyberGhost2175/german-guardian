"""
Telegram Bot Webhook — принимает команды от пользователя
POST /api/webhook
"""

import os
import json
import asyncio
import logging
from http.server import BaseHTTPRequestHandler
import redis
from telegram import Bot, Update
from telegram.constants import ParseMode

log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
REDIS_URL        = os.environ["REDIS_URL"]

def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

async def handle_update(data: dict):
    bot = Bot(token=TELEGRAM_TOKEN)
    message = data.get("message") or data.get("edited_message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()

    # Защита — только владелец
    if chat_id != TELEGRAM_CHAT_ID:
        await bot.send_message(chat_id=chat_id, text="⛔ Нет доступа")
        return

    r = get_redis()

    # ── /start ──
    if text == "/start":
        await bot.send_message(
            chat_id=chat_id,
            parse_mode=ParseMode.HTML,
            text=(
                "👋 <b>Instagram Following Tracker</b>\n\n"
                "Команды:\n"
                "▶️ /track username — начать следить\n"
                "📊 /status — за кем слежу сейчас\n"
                "⏹ /stop — остановить слежку\n"
                "🔄 /check — проверить прямо сейчас\n\n"
                "<i>Пример: /track cristiano</i>"
            )
        )

    # ── /track username ──
    elif text.startswith("/track"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(chat_id=chat_id, text="❌ Укажи username\nПример: /track cristiano")
            return

        username = parts[1].lstrip("@").lower()
        current = r.get("target_username")

        if current == username:
            await bot.send_message(chat_id=chat_id, text=f"ℹ️ Уже слежу за @{username}")
            return

        # Сохраняем нового таргета, сбрасываем базу подписок
        r.set("target_username", username)
        r.delete("following_db")

        await bot.send_message(
            chat_id=chat_id,
            parse_mode=ParseMode.HTML,
            text=(
                f"✅ Переключился на <b>@{username}</b>\n"
                f"База сброшена — при следующей проверке сохраню текущие подписки как точку отсчёта\n\n"
                f"Напиши /check чтобы инициализировать прямо сейчас"
            )
        )

    # ── /status ──
    elif text == "/status":
        r = get_redis()
        target = r.get("target_username")
        db_json = r.get("following_db")
        count = len(json.loads(db_json)) if db_json else 0

        if target:
            await bot.send_message(
                chat_id=chat_id,
                parse_mode=ParseMode.HTML,
                text=(
                    f"📊 <b>Статус</b>\n\n"
                    f"👤 Слежу за: <b>@{target}</b>\n"
                    f"📌 Подписок в базе: <b>{count}</b>\n"
                    f"⏰ Проверка каждые <b>2 часа</b>"
                )
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="ℹ️ Пока ни за кем не слежу\nНапиши /track username"
            )

    # ── /stop ──
    elif text == "/stop":
        target = r.get("target_username")
        if target:
            r.delete("target_username")
            r.delete("following_db")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⏹ Остановил слежку за @{target}"
            )
        else:
            await bot.send_message(chat_id=chat_id, text="ℹ️ Слежка и так не запущена")

    # ── /check — ручная проверка прямо сейчас ──
    elif text == "/check":
        target = r.get("target_username")
        if not target:
            await bot.send_message(chat_id=chat_id, text="❌ Сначала укажи username через /track")
            return

        await bot.send_message(
            chat_id=chat_id,
            text=f"🔄 Запускаю проверку @{target}..."
        )

        # Вызываем логику проверки
        from api.check import run_check
        result = run_check()

        if result.get("status") == "initialized":
            await bot.send_message(
                chat_id=chat_id,
                parse_mode=ParseMode.HTML,
                text=f"📦 База инициализирована\nСохранено <b>{result['count']}</b> подписок\nТеперь буду отслеживать новые!"
            )
        elif result.get("new", 0) > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Готово! Найдено {result['new']} новых подписок (отправил выше)"
            )
        else:
            await bot.send_message(chat_id=chat_id, text="✅ Проверил — новых подписок нет")

    else:
        await bot.send_message(
            chat_id=chat_id,
            text="❓ Не понял команду\nНапиши /start чтобы увидеть список команд"
        )


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            asyncio.run(handle_update(data))
        except Exception as e:
            log.error(f"Webhook error: {e}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass

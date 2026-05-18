"""
Instagram Following Tracker — Vercel Cron Handler
Запускается каждые 2 часа через Vercel Cron Jobs
POST /api/check
"""

import os
import json
import asyncio
import logging
from http.server import BaseHTTPRequestHandler
from instagrapi import Client
from telegram import Bot
from telegram.constants import ParseMode
import redis

log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
INSTA_LOGIN      = os.environ["INSTA_LOGIN"]
INSTA_PASSWORD   = os.environ["INSTA_PASSWORD"]
REDIS_URL        = os.environ["REDIS_URL"]
CRON_SECRET      = os.environ.get("CRON_SECRET", "")

def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

def get_instagram_client() -> Client:
    r = get_redis()
    cl = Client()
    cl.delay_range = [2, 5]

    session_json = r.get("insta_session")
    if session_json:
        try:
            cl.set_settings(json.loads(session_json))
            cl.login(INSTA_LOGIN, INSTA_PASSWORD)
            log.info("✅ Сессия загружена из Redis")
            return cl
        except Exception:
            log.warning("Сессия устарела, перелогиниваемся...")

    cl.login(INSTA_LOGIN, INSTA_PASSWORD)
    r.set("insta_session", json.dumps(cl.get_settings()))
    log.info("✅ Новая сессия сохранена в Redis")
    return cl

def get_following(cl: Client, username: str) -> dict:
    user_id = cl.user_id_from_username(username)
    following = cl.user_following(user_id, amount=0)
    result = {}
    for uid, user in following.items():
        result[str(uid)] = {
            "username": user.username,
            "full_name": user.full_name or "",
            "profile_pic_url": str(user.profile_pic_url) if user.profile_pic_url else "",
        }
    log.info(f"Получено {len(result)} подписок у @{username}")
    return result

async def send_new_followings(new_users: list, target: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    header = (
        f"👀 <b>Новые подписки @{target}</b>\n"
        f"Найдено: <b>{len(new_users)}</b> новых\n"
        f"{'─' * 25}"
    )
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=header, parse_mode=ParseMode.HTML)

    for user in new_users:
        username = user["username"]
        full_name = user["full_name"]
        pic_url   = user["profile_pic_url"]
        link      = f"https://www.instagram.com/{username}/"
        caption   = (
            f"➕ <b><a href='{link}'>@{username}</a></b>\n"
            f"{'👤 ' + full_name if full_name else ''}"
        )
        try:
            if pic_url:
                await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=pic_url,
                                     caption=caption, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=caption,
                                       parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"Фото не отправилось для @{username}: {e}")
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=caption,
                                   parse_mode=ParseMode.HTML)
        await asyncio.sleep(0.5)

async def send_message(text: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.HTML)

def run_check() -> dict:
    r = get_redis()

    target = r.get("target_username")
    if not target:
        log.info("Таргет не задан, пропускаем")
        return {"status": "no_target"}

    log.info(f"🔄 Проверяю подписки @{target}...")

    cl = get_instagram_client()
    current = get_following(cl, target)
    saved_json = r.get("following_db")
    saved = json.loads(saved_json) if saved_json else {}

    if not saved:
        r.set("following_db", json.dumps(current))
        count = len(current)
        log.info(f"Первый запуск: сохранено {count} подписок")
        asyncio.run(send_message(
            f"📦 <b>База инициализирована</b>\n"
            f"Слежу за <b>@{target}</b>\n"
            f"Сохранено подписок: <b>{count}</b>\n"
            f"Теперь буду присылать новые!"
        ))
        return {"status": "initialized", "count": count}

    new_ids = set(current.keys()) - set(saved.keys())
    if new_ids:
        new_users = [current[uid] for uid in new_ids]
        log.info(f"🆕 Найдено {len(new_users)} новых подписок")
        asyncio.run(send_new_followings(new_users, target))
    else:
        log.info("Новых подписок нет")

    r.set("following_db", json.dumps(current))
    return {"status": "ok", "new": len(new_ids)}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        try:
            result = run_check()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            log.error(f"Ошибка: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass

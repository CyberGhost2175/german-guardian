"""
Instagram Following Tracker — Vercel Cron Handler
Запускается каждые 2 часа через cron-job.org
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

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
INSTA_LOGIN    = os.environ["INSTA_LOGIN"]
INSTA_PASSWORD = os.environ["INSTA_PASSWORD"]
REDIS_URL      = os.environ["REDIS_URL"]
CRON_SECRET    = os.environ.get("CRON_SECRET", "")

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

async def send_new_followings(chat_id: str, new_users: list, target: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    header = (
        f"👀 <b>Новые подписки @{target}</b>\n"
        f"Найдено: <b>{len(new_users)}</b> новых\n"
        f"{'─' * 25}"
    )
    await bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)

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
                await bot.send_photo(chat_id=chat_id, photo=pic_url,
                                     caption=caption, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id=chat_id, text=caption,
                                       parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"Фото не отправилось для @{username}: {e}")
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML)
        await asyncio.sleep(0.5)

async def send_message(chat_id: str, text: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

def run_check_for_user(chat_id: str, target: str) -> dict:
    """Проверка для конкретного пользователя (вызывается из webhook /check)"""
    r = get_redis()
    key_db = f"following:{chat_id}"

    cl = get_instagram_client()
    current = get_following(cl, target)
    saved_json = r.get(key_db)
    saved = json.loads(saved_json) if saved_json else {}

    if not saved:
        r.set(key_db, json.dumps(current))
        count = len(current)
        asyncio.run(send_message(
            chat_id,
            f"📦 <b>База инициализирована</b>\n"
            f"Слежу за <b>@{target}</b>\n"
            f"Сохранено подписок: <b>{count}</b>"
        ))
        return {"status": "initialized", "count": count}

    new_ids = set(current.keys()) - set(saved.keys())
    if new_ids:
        new_users = [current[uid] for uid in new_ids]
        asyncio.run(send_new_followings(chat_id, new_users, target))

    r.set(key_db, json.dumps(current))
    return {"status": "ok", "new": len(new_ids)}

def run_check_all() -> dict:
    """Крон — проверяет всех пользователей у кого есть таргет"""
    r = get_redis()
    keys = r.keys("target:*")

    if not keys:
        log.info("Нет активных пользователей")
        return {"status": "no_users"}

    cl = get_instagram_client()
    total_new = 0

    for key in keys:
        chat_id = key.split(":", 1)[1]
        target = r.get(key)
        if not target:
            continue

        try:
            log.info(f"Проверяю @{target} для {chat_id}")
            key_db = f"following:{chat_id}"
            current = get_following(cl, target)
            saved_json = r.get(key_db)
            saved = json.loads(saved_json) if saved_json else {}

            if not saved:
                r.set(key_db, json.dumps(current))
                asyncio.run(send_message(
                    chat_id,
                    f"📦 База инициализирована для <b>@{target}</b>\n"
                    f"Сохранено: <b>{len(current)}</b> подписок"
                ))
                continue

            new_ids = set(current.keys()) - set(saved.keys())
            if new_ids:
                new_users = [current[uid] for uid in new_ids]
                asyncio.run(send_new_followings(chat_id, new_users, target))
                total_new += len(new_ids)

            r.set(key_db, json.dumps(current))

        except Exception as e:
            log.error(f"Ошибка для {chat_id}/@{target}: {e}")

    return {"status": "ok", "total_new": total_new}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        try:
            result = run_check_all()
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

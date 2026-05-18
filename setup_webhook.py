"""
Запусти этот скрипт ОДИН РАЗ после деплоя на Vercel
чтобы зарегистрировать webhook в Telegram

Использование:
  python setup_webhook.py ТОКЕН https://твой-сайт.vercel.app
"""

import sys
import urllib.request
import json

if len(sys.argv) < 3:
    print("Использование: python setup_webhook.py ТОКЕН https://твой-сайт.vercel.app")
    sys.exit(1)

token = sys.argv[1]
base_url = sys.argv[2].rstrip("/")
webhook_url = f"{base_url}/api/webhook"

url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"
with urllib.request.urlopen(url) as response:
    result = json.loads(response.read())
    if result.get("ok"):
        print(f"✅ Webhook установлен: {webhook_url}")
    else:
        print(f"❌ Ошибка: {result}")

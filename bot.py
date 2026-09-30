# ============================================================
# QuestLife Bot — регистрация Mini App + webhook для /start
# Запускать ОДИН раз: python bot.py
# ============================================================

import os

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = "https://questlife-production.up.railway.app"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def main():
    # 1. Кнопка в меню (слева от поля ввода)
    res = requests.post(
        f"{API}/setChatMenuButton",
        json={
            "menu_button": {
                "type": "web_app",
                "text": "QuestLife",
                "web_app": {"url": WEBAPP_URL},
            },
        },
    )
    print("setChatMenuButton:", res.status_code, res.json())

    # 2. Webhook: Railway сам отвечает на /start
    res = requests.post(
        f"{API}/setWebhook",
        json={"url": f"{WEBAPP_URL}/webhook"},
    )
    print("setWebhook:", res.status_code, res.json())

    # 3. Инфо о боте
    me = requests.get(f"{API}/getMe")
    print("getMe:", me.status_code, me.json())


if __name__ == "__main__":
    main()

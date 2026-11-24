"""Настройки проекта: всё секретное читаем из .env."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SHEET_ID = os.getenv("SHEET_ID", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
# Ответы админа кнопками + «Мої заявки». Папа решает всё по телефону, поэтому по умолчанию выключено.
ADMIN_REPLIES = os.getenv("ADMIN_REPLIES", "0").strip().lower() in ("1", "true", "yes")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "credentials.json")

# Проверяем сразу при старте, чтобы не ловить непонятные ошибки потом
_missing = [name for name, value in {"BOT_TOKEN": BOT_TOKEN, "SHEET_ID": SHEET_ID}.items() if not value]
if _missing:
    raise SystemExit(f"Не заполнено в .env: {', '.join(_missing)} (смотри .env.example)")

"""Настройки проекта: всё секретное читаем из .env (в демо-режиме — из .env.demo)."""
import os
import re
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Демо-копия для записи видео: python bot.py --demo
# Свой тестовый бот, своя таблица и своя папка настроек — настоящий бот не затрагивается.
DEMO = "--demo" in sys.argv
if DEMO:
    ENV_FILE = BASE_DIR / ".env.demo"
    if not ENV_FILE.exists():
        raise SystemExit("Нет файла .env.demo (смотри README, раздел Demo mode)")
    load_dotenv(ENV_FILE, override=True, encoding="utf-8-sig")
    os.environ["SETTINGS_FILE"] = str(BASE_DIR / "data" / "demo" / "settings.json")
else:
    ENV_FILE = BASE_DIR / ".env"
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
    raise SystemExit(f"Не заполнено в {ENV_FILE.name}: {', '.join(_missing)} (смотри .env.example)")


def bot_id(token: str) -> str:
    """Номер бота — часть токена до двоеточия (не меняется при перевыпуске токена)."""
    return token.split(":", 1)[0].strip()


def sheet_key(value: str) -> str:
    """ID таблицы, даже если вставили всю ссылку."""
    found = re.search(r"/d/([\w-]+)", value)
    return found.group(1) if found else value.strip()


def demo_conflicts(demo: dict, prod: dict) -> list[str]:
    """Что в демо-настройках совпадает с настоящими (так быть не должно)."""
    same = []
    for name, normalize in (("BOT_TOKEN", bot_id), ("SHEET_ID", sheet_key)):
        real = prod.get(name) or ""
        if real and normalize(demo.get(name) or "") == normalize(real):
            same.append(name)
    return same


if DEMO:
    _same = demo_conflicts({"BOT_TOKEN": BOT_TOKEN, "SHEET_ID": SHEET_ID},
                           dotenv_values(BASE_DIR / ".env", encoding="utf-8-sig"))
    if _same:
        raise SystemExit(f"СТОП: в .env.demo то же, что в настоящем .env: {', '.join(_same)}. "
                         "Нужны отдельный тестовый бот и отдельная таблица.")

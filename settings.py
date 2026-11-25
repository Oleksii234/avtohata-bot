"""
Настройки, которые меняются во время работы бота (а не в .env):
кто сейчас админ и одноразовая ссылка для передачи бота.
Хранятся в маленьком JSON-файле, поэтому переживают перезапуск.
Модуль ничего не знает про Telegram.
"""
import json
import os
import secrets
import time
from pathlib import Path

import config

SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", Path(__file__).resolve().parent / "data" / "settings.json"))
HANDOVER_TTL = 7 * 24 * 3600  # ссылка на передачу действует неделю


def _load() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)  # атомарная замена — файл не побьётся при сбое


def admin_id() -> int:
    """Текущий админ: после передачи — из файла, иначе ADMIN_ID из .env."""
    return int(_load().get("admin_id") or config.ADMIN_ID)


def is_admin(user_id: int) -> bool:
    return bool(user_id) and user_id == admin_id()


def new_handover_token() -> str:
    """Создаёт одноразовый код для ссылки t.me/<бот>?start=admin_<код>. Старый код перестаёт работать."""
    data = _load()
    token = secrets.token_urlsafe(16)
    data["handover"] = {"token": token, "expires": time.time() + HANDOVER_TTL}
    _save(data)
    return token


def use_handover_token(token: str, user_id: int) -> int | None:
    """Если код верный и не просрочен — делает user_id админом.
    Возвращает ID прежнего админа (чтобы его предупредить) или None, если код не подошёл."""
    data = _load()
    handover = data.get("handover") or {}
    if not token or not secrets.compare_digest(token, handover.get("token", "")):
        return None
    if time.time() > handover.get("expires", 0):
        return None
    previous = admin_id()
    data["admin_id"] = user_id
    data.pop("handover", None)  # одноразовая: второй раз не сработает
    _save(data)
    return previous

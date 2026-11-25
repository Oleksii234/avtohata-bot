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
    (data.get("colleagues") or {}).pop(str(user_id), None)  # коллега стал админом
    data.pop("handover", None)  # одноразовая: второй раз не сработает
    _save(data)
    return previous


# --- Колеги (например, Олег): получают заявки, только когда админ нажмёт «Надіслати …» ---

def colleagues() -> dict[int, str]:
    """{telegram_id: имя} всех коллег."""
    return {int(k): v for k, v in (_load().get("colleagues") or {}).items()}


def is_colleague(user_id: int) -> bool:
    return user_id in colleagues()


def is_staff(user_id: int) -> bool:
    """Админ или коллега — тот, кто может отвечать на заявки."""
    return is_admin(user_id) or is_colleague(user_id)


def staff_name(user_id: int) -> str:
    return colleagues().get(user_id, "Адмін")


def new_colleague_token() -> str:
    """Одноразовый код для ссылки t.me/<бот>?start=staff_<код>."""
    data = _load()
    token = secrets.token_urlsafe(16)
    data["staff_invite"] = {"token": token, "expires": time.time() + HANDOVER_TTL}
    _save(data)
    return token


def use_colleague_token(token: str, user_id: int, name: str) -> bool:
    data = _load()
    invite = data.get("staff_invite") or {}
    if not token or not secrets.compare_digest(token, invite.get("token", "")):
        return False
    if time.time() > invite.get("expires", 0) or user_id == admin_id():
        return False
    data.setdefault("colleagues", {})[str(user_id)] = name
    data.pop("staff_invite", None)
    _save(data)
    return True


def remove_colleague(user_id: int) -> str | None:
    data = _load()
    name = (data.get("colleagues") or {}).pop(str(user_id), None)
    data["assigned"] = {k: v for k, v in (data.get("assigned") or {}).items() if v != user_id}
    _save(data)
    return name


def assign(number: int, user_id: int) -> None:
    """Заявку №number передали коллеге user_id."""
    data = _load()
    data.setdefault("assigned", {})[str(number)] = user_id
    _save(data)


def assigned_to(number: int) -> int | None:
    value = (_load().get("assigned") or {}).get(str(number))
    return int(value) if value else None


def can_handle(user_id: int, number: int) -> bool:
    """Админ — любую заявку, коллега — только переданную ему."""
    return is_admin(user_id) or (is_colleague(user_id) and assigned_to(number) == user_id)


def staff_for(number: int) -> list[int]:
    """Кому сообщать о событиях по заявке: админ + коллега, если заявку передали."""
    ids = [admin_id()]
    colleague = assigned_to(number)
    if colleague and colleague not in ids and is_colleague(colleague):
        ids.append(colleague)
    return [i for i in ids if i]

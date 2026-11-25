"""
Сценарии диалогов без интернета: Telegram и Google Таблица подменены фейками.
Запуск: .venv\\Scripts\\python.exe -m pytest -q
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Фейковые настройки — до импорта модулей бота (config читает их при импорте)
os.environ.update(BOT_TOKEN="123456:" + "A" * 35, SHEET_ID="test", ADMIN_ID="1000", ADMIN_REPLIES="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Contact, Message, PhotoSize, Update, User  # noqa: E402

import admin  # noqa: E402
import bot as botmod  # noqa: E402
import config  # noqa: E402
import form  # noqa: E402
import sheets  # noqa: E402

ADMIN, CLIENT, BLOCKED = 1000, 555, 777


# --- Фейковая таблица ---
class FakeSheet:
    def __init__(self):
        self.rows: list[dict] = []

    async def append_request(self, data):
        n = len(self.rows) + 1
        self.rows.append({
            "number": str(n), "date": "25.11.2026 10:00", "client": data["client"],
            "phone": data["phone"], "tg_id": str(data["tg_id"]), "car": data["car"],
            "year": data["year"], "vin": data["vin"], "part": data["part"],
            "status": "Нова", "price": "", "term": "",
        })
        return n

    async def get_request(self, n):
        return next((dict(r) for r in self.rows if r["number"] == str(n)), None)

    async def update_answer(self, n, status, price="", term=""):
        for r in self.rows:
            if r["number"] == str(n):
                r.update(status=status, price=price, term=term)
                return True
        return False

    async def client_requests(self, tg_id, limit=5):
        return [dict(r) for r in self.rows if r["tg_id"] == str(tg_id)][-limit:]


# --- Фейковый Telegram: запоминаем, что бот отправил ---
class FakeBot(Bot):
    def __init__(self):
        super().__init__(os.environ["BOT_TOKEN"])
        self.sent: list = []
        self.blocked: set[int] = set()

    async def __call__(self, method, request_timeout=None):
        chat = getattr(method, "chat_id", None)
        if chat in self.blocked and type(method).__name__ == "SendMessage":
            raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
        self.sent.append(method)
        return True

    def texts(self, chat=None):
        return [getattr(m, "text", None) or getattr(m, "caption", None) or ""
                for m in self.sent if chat is None or getattr(m, "chat_id", None) == chat]


dp = botmod.dp
dp.include_router(admin.router)
dp.include_router(form.router)
_ids = iter(range(1, 10**6))


@pytest.fixture
def env(monkeypatch):
    sheet = FakeSheet()
    for name in ("append_request", "get_request", "update_answer", "client_requests"):
        monkeypatch.setattr(sheets, name, getattr(sheet, name))
    fake = FakeBot()
    dp.fsm.storage = MemoryStorage()  # чистые состояния FSM в каждом тесте
    return sheet, fake


def user(i):
    return User(id=i, is_bot=False, first_name=f"U{i}")


def send(fake, i, text=None, **kw):
    fake.sent.clear()
    n = next(_ids)
    msg = Message(message_id=n, date=datetime.now(), chat=Chat(id=i, type="private"),
                  from_user=user(i), text=text, **kw)
    asyncio.run(dp.feed_update(fake, Update(update_id=n, message=msg)))
    return fake.texts()


def press(fake, i, data):
    fake.sent.clear()
    n = next(_ids)
    msg = Message(message_id=999, date=datetime.now(), chat=Chat(id=i, type="private"),
                  from_user=user(i), text="notif")
    cb = CallbackQuery(id=str(n), from_user=user(i), chat_instance="x", data=data, message=msg)
    asyncio.run(dp.feed_update(fake, Update(update_id=n, callback_query=cb)))
    return fake.texts()


def fill_form(fake, i, part="колодки"):
    send(fake, i, "🔧 Підібрати запчастину")
    for answer in ("VW Golf 6", "2010", "🤷 Не знаю VIN", part, "+380 67 123 45 67"):
        send(fake, i, answer)
    return send(fake, i, "✅ Надіслати заявку")


# --- Тесты ---
def test_form_validation_and_send(env):
    sheet, fake = env
    send(fake, CLIENT, "🔧 Підібрати запчастину")
    assert "марку і модель" in send(fake, CLIENT, "x")[0]
    send(fake, CLIENT, "Audi A4")
    assert "чотирма цифрами" in send(fake, CLIENT, "20")[0]
    send(fake, CLIENT, "2008")
    assert "17 символів" in send(fake, CLIENT, "123")[0]
    send(fake, CLIENT, photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)])
    send(fake, CLIENT, "<b>фільтр</b> & масло")
    assert "Не схоже на номер" in send(fake, CLIENT, "abc")[0]
    summary = send(fake, CLIENT, contact=Contact(phone_number="380671234567", first_name="X"))[0]
    assert "&lt;b&gt;" in summary and "+380671234567" in summary  # HTML экранирован, «+» добавлен
    send(fake, CLIENT, "✅ Надіслати заявку")
    assert sheet.rows[0]["vin"] == "фото техпаспорта"
    assert any(type(m).__name__ == "SendPhoto" and m.chat_id == ADMIN for m in fake.sent)


def test_repeat_request_same_car(env):
    sheet, fake = env
    fill_form(fake, CLIENT)
    assert "Шукаємо для того самого авто?" in send(fake, CLIENT, "🔧 Підібрати запчастину")[0]
    send(fake, CLIENT, "✅ Те саме авто")
    assert "Перевірте заявку" in send(fake, CLIENT, "масляний фільтр")[0]  # телефон не спрашиваем
    send(fake, CLIENT, "✅ Надіслати заявку")
    assert sheet.rows[1]["car"] == "VW Golf 6" and sheet.rows[1]["phone"] == "+380671234567"


def test_admin_in_stock(env):
    sheet, fake = env
    fill_form(fake, CLIENT)
    assert press(fake, CLIENT, "adm:stock:1") == []  # не админ — игнор
    press(fake, ADMIN, "adm:stock:1")
    assert "лише число" in send(fake, ADMIN, "дорого")[0]
    send(fake, ADMIN, "1 200 грн")
    assert "1200 грн" in fake.texts(CLIENT)[0]
    assert sheet.rows[0]["status"] == "Є в наявності" and sheet.rows[0]["price"] == "1200 грн"
    assert "вже є відповідь" in press(fake, ADMIN, "adm:order:1")[0]  # второй раз клиенту не шлём


def test_admin_order_and_none(env):
    sheet, fake = env
    fill_form(fake, CLIENT, "амортизатори")
    fill_form(fake, 888, "свічки")
    press(fake, ADMIN, "adm:order:1")
    send(fake, ADMIN, "2500")
    send(fake, ADMIN, "Протягом години")
    assert sheet.rows[0]["term"] == "Протягом години"
    press(fake, ADMIN, "adm:stock:2")
    assert "скасовано" in send(fake, ADMIN, "/cancel")[0]
    press(fake, ADMIN, "adm:none:2")
    assert "На жаль" in fake.texts(888)[0] and sheet.rows[1]["status"] == "Немає"


def test_blocked_client(env):
    _, fake = env
    fill_form(fake, BLOCKED)
    fake.blocked.add(BLOCKED)
    press(fake, ADMIN, "adm:stock:1")
    assert "заблокував" in send(fake, ADMIN, "900")[-1]


def test_my_requests(env):
    _, fake = env
    assert "ще немає заявок" in send(fake, CLIENT, "📋 Мої заявки")[0]
    fill_form(fake, CLIENT)
    assert "№1" in send(fake, CLIENT, "📋 Мої заявки")[0]


def test_simple_mode(env, monkeypatch):
    _, fake = env
    monkeypatch.setattr(config, "ADMIN_REPLIES", False)
    fill_form(fake, CLIENT)
    notification = next(m for m in fake.sent if m.chat_id == ADMIN)
    assert notification.reply_markup is None and "Зателефонуйте" in notification.text
    assert "Сюди приходитимуть нові заявки" in send(fake, ADMIN, "/start")[0]
    assert "Вітаємо в АвтоХаті" in send(fake, CLIENT, "/start")[0]


def test_phone_normalized_for_tap_to_call(env):
    sheet, fake = env
    fill_form(fake, CLIENT)  # номер вводится как «+380 67 123 45 67»
    assert sheet.rows[0]["phone"] == "+380671234567"
    assert form.normalize_phone("067 123-45-67") == "+380671234567"
    assert form.normalize_phone("380671234567") == "+380671234567"

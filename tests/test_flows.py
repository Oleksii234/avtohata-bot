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
import notes  # noqa: E402
import settings  # noqa: E402
import sheets  # noqa: E402

ADMIN, CLIENT, BLOCKED = 1000, 555, 777


# --- Фейковая таблица ---
class FakeSheet:
    def __init__(self):
        self.rows: list[dict] = []
        self.notes: dict[str, str] = {}  # лист «Клієнти»

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

    async def update_status(self, n, status, comment=None):
        for r in self.rows:
            if r["number"] == str(n):
                r["status"] = status
                if comment is not None:
                    r["comment"] = comment
                return True
        return False

    async def get_note(self, tg_id):
        return self.notes.get(str(tg_id), "")

    async def set_note(self, tg_id, client, phone, note):
        self.notes[str(tg_id)] = note

    async def client_requests(self, tg_id, limit=5):
        return [dict(r) for r in self.rows if r["tg_id"] == str(tg_id)][-limit:]


# --- Фейковый Telegram: запоминаем, что бот отправил ---
class FakeBot(Bot):
    def __init__(self):
        super().__init__(os.environ["BOT_TOKEN"])
        self.sent: list = []
        self.blocked: set[int] = set()

    async def __call__(self, method, request_timeout=None):
        if type(method).__name__ == "GetMe":  # нужно для create_start_link
            return User(id=1, is_bot=True, first_name="Bot", username="avtohata_zt_bot")
        chat = getattr(method, "chat_id", None)
        if chat in self.blocked and type(method).__name__ == "SendMessage":
            raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
        self.sent.append(method)
        return True

    def texts(self, chat=None):
        return [getattr(m, "text", None) or getattr(m, "caption", None) or ""
                for m in self.sent if chat is None or getattr(m, "chat_id", None) == chat]


dp = botmod.dp
dp.include_router(notes.router)
dp.include_router(admin.router)
dp.include_router(admin.client_router)
dp.include_router(form.router)
_ids = iter(range(1, 10**6))


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")  # чистые настройки
    sheet = FakeSheet()
    for name in ("append_request", "get_request", "update_answer", "update_status", "client_requests",
                 "get_note", "set_note"):
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
    assert press(fake, CLIENT, "adm:stock:1:0") == []  # не админ — игнор
    press(fake, ADMIN, "adm:stock:1:0")
    assert "лише число" in send(fake, ADMIN, "дорого")[0]
    send(fake, ADMIN, "1 200 грн")
    assert "1200 грн" in fake.texts(CLIENT)[0]
    assert sheet.rows[0]["status"] == "Є в наявності" and sheet.rows[0]["price"] == "1200 грн"
    assert "вже є відповідь" in press(fake, ADMIN, "adm:order:1:0")[0]  # второй раз клиенту не шлём


def test_admin_order_and_none(env):
    sheet, fake = env
    fill_form(fake, CLIENT, "амортизатори")
    fill_form(fake, 888, "свічки")
    press(fake, ADMIN, "adm:order:1:0")
    send(fake, ADMIN, "2500")
    send(fake, ADMIN, "Протягом години")
    assert sheet.rows[0]["term"] == "Протягом години"
    press(fake, ADMIN, "adm:stock:2:0")
    assert "скасовано" in send(fake, ADMIN, "/cancel")[0]
    press(fake, ADMIN, "adm:none:2:0")
    assert "На жаль" in fake.texts(888)[0] and sheet.rows[1]["status"] == "Немає"


def test_blocked_client(env):
    _, fake = env
    fill_form(fake, BLOCKED)
    fake.blocked.add(BLOCKED)
    press(fake, ADMIN, "adm:stock:1:0")
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
    assert notification.reply_markup.inline_keyboard == [[notification.reply_markup.inline_keyboard[0][0]]]
    assert notification.reply_markup.inline_keyboard[0][0].callback_data == "note:1"  # только «Підписати»
    assert "Зателефонуйте" in notification.text
    assert "Сюди приходитимуть нові заявки" in send(fake, ADMIN, "/start")[0]
    assert "Вітаємо в АвтоХаті" in send(fake, CLIENT, "/start")[0]


def test_phone_normalized_for_tap_to_call(env):
    sheet, fake = env
    fill_form(fake, CLIENT)  # номер вводится как «+380 67 123 45 67»
    assert sheet.rows[0]["phone"] == "+380671234567"
    assert form.normalize_phone("067 123-45-67") == "+380671234567"
    assert form.normalize_phone("380671234567") == "+380671234567"


DAD = 2000


def test_admin_start_has_no_client_menu(env):
    _, fake = env
    send(fake, ADMIN, "/start")
    greeting = fake.sent[0]
    assert "Під кожною заявкою будуть кнопки" in greeting.text
    assert type(greeting.reply_markup).__name__ == "ReplyKeyboardRemove"


def test_handover_link(env):
    sheet, fake = env
    assert send(fake, CLIENT, "/peredat") == []  # не админ — ссылку создать нельзя

    reply = send(fake, ADMIN, "/peredat")[0]
    link = reply.splitlines()[1]
    assert link.startswith("https://t.me/avtohata_zt_bot?start=admin_")
    payload = link.split("start=")[1]

    send(fake, DAD, "/start " + payload)  # папа открыл ссылку
    assert "Сюди приходитимуть нові заявки" in fake.texts(DAD)[0]
    assert "Бот передано" in fake.texts(ADMIN)[0]  # прежнего админа предупредили
    assert settings.admin_id() == DAD

    assert "вже використане" in send(fake, CLIENT, "/start " + payload)[0]  # одноразовая
    assert settings.admin_id() == DAD

    fill_form(fake, CLIENT)  # заявки теперь идут папе, с кнопками
    notification = next(m for m in fake.sent if getattr(m, "chat_id", None) == DAD)
    assert notification.reply_markup is not None
    assert press(fake, ADMIN, "adm:stock:1:0") == []  # у прежнего админа кнопки больше не работают
    press(fake, DAD, "adm:stock:1:0")
    send(fake, DAD, "1500")
    assert sheet.rows[0]["price"] == "1500 грн"


def test_handover_wrong_token(env):
    _, fake = env
    send(fake, ADMIN, "/peredat")
    assert "вже використане" in send(fake, CLIENT, "/start admin_wrong")[0]
    assert settings.admin_id() == ADMIN


# --- Под заказ: клиент «Беру / Не потрібно», потом «Привезли» ---

def order(fake, staff, number, price="2500", term="Завтра"):
    press(fake, staff, f"adm:order:{number}:0")
    send(fake, staff, price)
    return send(fake, staff, term)


def test_order_client_takes_and_part_arrives(env):
    sheet, fake = env
    fill_form(fake, CLIENT)
    order(fake, ADMIN, 1)
    offer = next(m for m in fake.sent if getattr(m, "chat_id", None) == CLIENT)
    assert "Замовляємо?" in offer.text and "зателефонуємо" not in offer.text
    assert offer.reply_markup.inline_keyboard[0][0].callback_data == "cli:yes:1"

    assert "Заявку не знайдено" in press(fake, 888, "cli:yes:1")[0]  # чужой клиент не может
    press(fake, CLIENT, "cli:yes:1")
    assert sheet.rows[0]["status"] == "Клієнт бере — замовити"
    to_admin = next(m for m in fake.sent if getattr(m, "chat_id", None) == ADMIN)
    assert "Клієнт бере" in to_admin.text
    assert to_admin.reply_markup.inline_keyboard[0][0].callback_data == "adm:arrived:1:0"
    assert any("вже отримано" in t for t in press(fake, CLIENT, "cli:no:1"))  # передумать кнопкой нельзя

    press(fake, ADMIN, "adm:arrived:1:0")
    assert "можна забирати" in fake.texts(CLIENT)[0]
    assert sheet.rows[0]["status"] == "Привезли — можна забирати"
    assert "Клієнту вже не потрібно писати" in press(fake, ADMIN, "adm:arrived:1:0")[0]  # второй раз — нет


def test_order_client_declines(env):
    sheet, fake = env
    fill_form(fake, CLIENT)
    order(fake, ADMIN, 1)
    press(fake, CLIENT, "cli:no:1")
    assert sheet.rows[0]["status"] == "Клієнт відмовився"
    assert "замовляти не потрібно" in fake.texts(ADMIN)[0]


# --- Колега (Олег): получает заявку, только если админ нажал «Надіслати» ---
OLEG = 3000


def add_oleg(fake):
    # Ссылку делаем на сервере (у папы кнопки «Додати колегу» нет), Олег её открывает
    send(fake, OLEG, "/start staff_" + settings.new_colleague_token())


def test_admin_start_has_no_staff_panel(env):
    _, fake = env
    send(fake, ADMIN, "/start")
    assert len(fake.sent) == 1 and type(fake.sent[0].reply_markup).__name__ == "ReplyKeyboardRemove"


def test_colleague_link_via_hidden_command(env):
    _, fake = env
    send(fake, ADMIN, "/kolegy")
    link = next(t for t in press(fake, ADMIN, "stf:add:0") if "t.me/" in t).splitlines()[1]
    send(fake, OLEG, "/start " + link.split("start=")[1])
    assert OLEG in settings.colleagues()


def test_colleague_invite_and_forward(env):
    sheet, fake = env
    add_oleg(fake)
    assert "Ви колега" in fake.texts(OLEG)[0]
    assert "тепер колега" in fake.texts(ADMIN)[0]
    assert settings.colleagues() == {OLEG: f"U{OLEG}"}
    assert "Ви колега" in send(fake, OLEG, "/start")[0]  # у коллеги нет клиентского меню

    fill_form(fake, CLIENT)
    assert fake.texts(OLEG) == []  # сам по себе коллеге ничего не приходит
    notification = next(m for m in fake.sent if getattr(m, "chat_id", None) == ADMIN)
    assert notification.reply_markup.inline_keyboard[-2][0].callback_data == f"adm:fwd:1:{OLEG}"

    assert "не передана" in press(fake, OLEG, "adm:stock:1:0")[0]  # пока не передали — нельзя
    press(fake, ADMIN, f"adm:fwd:1:{OLEG}")
    to_oleg = next(m for m in fake.sent if getattr(m, "chat_id", None) == OLEG)
    assert "Вам передали заявку" in to_oleg.text
    buttons = [r[0].callback_data for r in to_oleg.reply_markup.inline_keyboard]
    assert buttons == ["adm:stock:1:0", "adm:order:1:0", "adm:none:1:0", "note:1"]  # без пересылки

    order(fake, OLEG, 1, price="900", term="Протягом години")
    assert sheet.rows[0]["price"] == "900 грн" and sheet.rows[0]["comment"] == f"Відповів: U{OLEG}"
    assert "відповів(ла) на заявку №1" in fake.texts(ADMIN)[0]  # папа видит ответ Олега

    press(fake, CLIENT, "cli:yes:1")
    assert "Клієнт бере" in fake.texts(ADMIN)[0] and "Клієнт бере" in fake.texts(OLEG)[0]
    press(fake, OLEG, "adm:arrived:1:0")
    assert "можна забирати" in fake.texts(CLIENT)[0]
    assert "привезли" in fake.texts(ADMIN)[0]


def test_colleague_cannot_forward_and_can_be_removed(env):
    _, fake = env
    add_oleg(fake)
    fill_form(fake, CLIENT)
    assert "лише адмін" in press(fake, OLEG, f"adm:fwd:1:{OLEG}")[0]
    press(fake, ADMIN, f"stf:del:{OLEG}")
    assert settings.colleagues() == {}
    assert press(fake, OLEG, "adm:stock:1:0") == []  # больше не сотрудник — кнопки молчат


# --- Подпись «хто це»: видят только сотрудники ---
SECRET = "Сусід Іван, бере на Ланос"


def test_note_shown_to_staff_never_to_client(env):
    sheet, fake = env
    add_oleg(fake)
    fill_form(fake, CLIENT)
    press(fake, ADMIN, "note:1")
    assert "клієнт його не бачить" in fake.texts(ADMIN)[0]
    assert "Збережено" in send(fake, ADMIN, SECRET)[0]
    assert sheet.notes[str(CLIENT)] == SECRET

    client_texts = []
    send(fake, CLIENT, "🔧 Підібрати запчастину")  # вторая заявка того же клиента
    client_texts += fake.texts(CLIENT)
    send(fake, CLIENT, "✅ Те саме авто")
    client_texts += fake.texts(CLIENT)
    send(fake, CLIENT, "фільтр")
    client_texts += fake.texts(CLIENT)
    send(fake, CLIENT, "✅ Надіслати заявку")
    client_texts += fake.texts(CLIENT)
    assert "Сусід Іван" in fake.texts(ADMIN)[0]  # папа видит подпись в новой заявке

    press(fake, ADMIN, f"adm:fwd:2:{OLEG}")
    assert "Сусід Іван" in fake.texts(OLEG)[0]  # коллега тоже видит

    order(fake, OLEG, 2)
    client_texts += fake.texts(CLIENT)
    press(fake, CLIENT, "cli:yes:2")
    client_texts += fake.texts(CLIENT)
    assert "Сусід Іван" in fake.texts(ADMIN)[0]  # и в «Клієнт бере»
    press(fake, ADMIN, "adm:arrived:2:0")
    client_texts += fake.texts(CLIENT)
    client_texts += send(fake, CLIENT, "📋 Мої заявки")
    assert client_texts and not any("Сусід Іван" in t or "Хто це" in t for t in client_texts)


def test_note_only_staff_can_write_and_delete(env):
    sheet, fake = env
    fill_form(fake, CLIENT)
    assert press(fake, CLIENT, "note:1") == []  # клиент не может подписывать
    press(fake, ADMIN, "note:1")
    send(fake, ADMIN, SECRET)
    press(fake, ADMIN, "note:1")
    assert "Зараз підписаний так" in fake.texts(ADMIN)[0]
    assert "видалено" in send(fake, ADMIN, "🗑 Видалити підпис")[0]
    assert sheet.notes[str(CLIENT)] == ""


# --- Демо-режим: не должен запуститься с настоящим ботом или настоящей таблицей ---
def test_demo_refuses_production_settings():
    prod = {"BOT_TOKEN": "111:old", "SHEET_ID": "https://docs.google.com/spreadsheets/d/abc_DEF-1/edit#gid=0"}
    # тот же бот с перевыпущенным токеном и та же таблица, вставленная только как ID
    assert config.demo_conflicts({"BOT_TOKEN": "111:new", "SHEET_ID": "abc_DEF-1"}, prod) == ["BOT_TOKEN", "SHEET_ID"]
    assert config.demo_conflicts({"BOT_TOKEN": "222:x", "SHEET_ID": "other"}, prod) == []
    assert config.demo_conflicts({"BOT_TOKEN": "222:x", "SHEET_ID": "other"}, {}) == []

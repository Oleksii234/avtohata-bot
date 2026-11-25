"""Клавиатуры и тексты кнопок (на украинском — для клиентов магазина)."""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

import config

BTN_REQUEST = "🔧 Підібрати запчастину"
BTN_CONTACTS = "📍 Контакти та графік"
BTN_MY_REQUESTS = "📋 Мої заявки"
BTN_CANCEL = "❌ Скасувати"
BTN_VIN_SKIP = "🤷 Не знаю VIN"
BTN_SEND = "✅ Надіслати заявку"
BTN_RESTART = "✏️ Заповнити заново"


def _kb(*rows: list[KeyboardButton]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=list(rows), resize_keyboard=True)


# «Мої заявки» имеет смысл только когда админ отвечает через бота (статусы обновляются)
MAIN_MENU = _kb(
    [KeyboardButton(text=BTN_REQUEST)],
    [KeyboardButton(text=BTN_MY_REQUESTS), KeyboardButton(text=BTN_CONTACTS)]
    if config.ADMIN_REPLIES else [KeyboardButton(text=BTN_CONTACTS)],
)

CANCEL_KB = _kb([KeyboardButton(text=BTN_CANCEL)])

VIN_KB = _kb(
    [KeyboardButton(text=BTN_VIN_SKIP)],
    [KeyboardButton(text=BTN_CANCEL)],
)

BTN_SAME_CAR = "✅ Те саме авто"
BTN_OTHER_CAR = "🚗 Інше авто"

# Повторная заявка: клиенту не нужно заново вводить авто, VIN и телефон
SAME_CAR_KB = _kb(
    [KeyboardButton(text=BTN_SAME_CAR)],
    [KeyboardButton(text=BTN_OTHER_CAR), KeyboardButton(text=BTN_CANCEL)],
)


def phone_kb(previous_phone: str = "") -> ReplyKeyboardMarkup:
    """request_contact=True — Telegram сам отправит номер по нажатию.
    Если клиент уже оставлял номер — даём кнопку с ним, чтобы не набирать."""
    rows = [[KeyboardButton(text="📱 Поділитися номером", request_contact=True)]]
    if previous_phone:
        rows.insert(0, [KeyboardButton(text=previous_phone)])
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return _kb(*rows)


PHONE_KB = phone_kb()

CONFIRM_KB = _kb(
    [KeyboardButton(text=BTN_SEND)],
    [KeyboardButton(text=BTN_RESTART), KeyboardButton(text=BTN_CANCEL)],
)


# --- Админ (папа) ---

class AdminCb(CallbackData, prefix="adm"):
    """Кнопки сотрудника по заявке: adm:<action>:<number>:<to>.
    action: stock / order / none — ответ клиенту; fwd — передать коллеге `to`;
    arrived — деталь привезли, сообщить клиенту."""
    action: str
    number: int
    to: int = 0


def admin_answer_kb(number: int, colleagues: dict[int, str] | None = None) -> InlineKeyboardMarkup:
    """Кнопки под уведомлением о новой заявке. colleagues — кому можно переслать (только у админа)."""
    def btn(text: str, action: str, to: int = 0) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text, callback_data=AdminCb(action=action, number=number, to=to).pack()
        )

    rows = [
        [btn("✅ Є в наявності", "stock")],
        [btn("📦 Під замовлення", "order")],
        [btn("❌ Немає", "none")],
    ]
    for user_id, name in (colleagues or {}).items():
        rows.append([btn(f"👨‍🔧 Надіслати: {name}", "fwd", user_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def arrived_kb(number: int) -> InlineKeyboardMarkup:
    """Под сообщением «клиент берёт»: нажать, когда деталь уже в магазине."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="✅ Привезли — повідомити клієнта",
        callback_data=AdminCb(action="arrived", number=number).pack(),
    )]])


# --- Клиент: ответ на «Під замовлення» ---

class ClientCb(CallbackData, prefix="cli"):
    """cli:<yes|no>:<number> — клиент берёт деталь под заказ или отказывается."""
    action: str
    number: int


def client_decision_kb(number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Беру, замовляйте",
                              callback_data=ClientCb(action="yes", number=number).pack())],
        [InlineKeyboardButton(text="❌ Не потрібно",
                              callback_data=ClientCb(action="no", number=number).pack())],
    ])


# --- Управление коллегами (у админа на /start) ---

class StaffCb(CallbackData, prefix="stf"):
    """stf:add:0 — ссылка-приглашение; stf:del:<id> — убрать коллегу."""
    action: str
    user: int = 0


def staff_manage_kb(colleagues: dict[int, str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Додати колегу", callback_data=StaffCb(action="add").pack())]]
    for user_id, name in colleagues.items():
        rows.append([InlineKeyboardButton(text=f"✖️ Прибрати колегу: {name}",
                                          callback_data=StaffCb(action="del", user=user_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


BTN_ADMIN_CANCEL = "↩️ Скасувати відповідь"

ADMIN_CANCEL_KB = _kb([KeyboardButton(text=BTN_ADMIN_CANCEL)])

# Готовые сроки — папе не нужно печатать, но можно написать и свой.
# Под заказ деталь обычно привозят за час, максимум — на следующий день.
TERM_OPTIONS = ["Протягом години", "2–3 години", "Сьогодні до вечора", "Завтра"]

TERM_KB = _kb(
    [KeyboardButton(text=TERM_OPTIONS[0]), KeyboardButton(text=TERM_OPTIONS[1])],
    [KeyboardButton(text=TERM_OPTIONS[2]), KeyboardButton(text=TERM_OPTIONS[3])],
    [KeyboardButton(text=BTN_ADMIN_CANCEL)],
)

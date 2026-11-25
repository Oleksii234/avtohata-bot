"""
Ответ админа (папы) на заявку.
Под уведомлением о заявке три кнопки: «Є» / «Під замовлення» / «Немає».
Бот спрашивает цену (и срок), отправляет ответ клиенту и записывает его в таблицу.
Все обработчики здесь работают только для текущего админа (settings.admin_id()).
"""
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import settings
import sheets
from keyboards import (
    ADMIN_CANCEL_KB, BTN_ADMIN_CANCEL, TERM_KB, AdminCb,
)
from texts import SHOP_CONTACTS, STATUS_IN_STOCK, STATUS_NEW, STATUS_NONE, STATUS_ORDER

router = Router()
# Фильтр на весь роутер: чужие нажатия и сообщения сюда не попадут
router.message.filter(F.from_user.id.func(settings.is_admin))
router.callback_query.filter(F.from_user.id.func(settings.is_admin))

# Админу клиентское меню не нужно — после ответа просто убираем клавиатуру
NO_KB = ReplyKeyboardRemove()

STATUS_BY_ACTION = {"stock": STATUS_IN_STOCK, "order": STATUS_ORDER, "none": STATUS_NONE}

# Цена: «1200», «1 200», «1200 грн», «1200,50»
PRICE_RE = re.compile(r"^\d{1,7}([.,]\d{1,2})?$")


class AdminAnswer(StatesGroup):
    price = State()
    term = State()


# --- Нажатие кнопки под уведомлением ---
@router.callback_query(AdminCb.filter())
async def on_answer_button(callback: CallbackQuery, callback_data: AdminCb,
                           state: FSMContext, bot: Bot) -> None:
    number = callback_data.number
    request = await sheets.get_request(number)
    if request is None:
        await callback.answer(f"Заявку №{number} не знайдено в таблиці", show_alert=True)
        return
    if request["status"] not in ("", STATUS_NEW):
        # Уже ответили (кнопкой или вручную в таблице) — не шлём клиенту второй раз
        await callback.answer(f"На заявку №{number} вже є відповідь: {request['status']}", show_alert=True)
        await _remove_buttons(bot, callback.message.chat.id, callback.message.message_id)
        return

    await callback.answer()
    await state.clear()
    await state.update_data(
        adm_action=callback_data.action, adm_number=number,
        adm_chat=callback.message.chat.id, adm_msg=callback.message.message_id,
    )

    if callback_data.action == "none":
        await _finish(callback.message, state, bot)
        return

    await state.set_state(AdminAnswer.price)
    await callback.message.answer(
        f"Заявка №{number}: <b>{html.escape(request['part'])}</b>\n"
        "💰 Напишіть ціну в гривнях, наприклад: <b>1200</b>",
        reply_markup=ADMIN_CANCEL_KB,
    )


# --- Отмена ввода ---
@router.message(StateFilter(AdminAnswer), F.text == BTN_ADMIN_CANCEL)
@router.message(StateFilter(AdminAnswer), Command("cancel"))
async def cancel_answer(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Відповідь скасовано. Кнопки під заявкою працюють, можна обрати знову.",
                         reply_markup=NO_KB)


@router.message(AdminAnswer.price, F.text)
async def step_price(message: Message, state: FSMContext, bot: Bot) -> None:
    raw = re.sub(r"\s|грн\.?", "", message.text.lower())
    if not PRICE_RE.match(raw):
        await message.answer("Напишіть лише число, наприклад: 1200")
        return
    await state.update_data(adm_price=f"{raw.replace('.', ',')} грн")

    data = await state.get_data()
    if data["adm_action"] == "stock":
        await _finish(message, state, bot)
        return
    await state.set_state(AdminAnswer.term)
    await message.answer("🚚 Коли буде деталь? Оберіть кнопку або напишіть свій варіант.", reply_markup=TERM_KB)


@router.message(AdminAnswer.term, F.text)
async def step_term(message: Message, state: FSMContext, bot: Bot) -> None:
    term = message.text.strip()
    if not 1 <= len(term) <= 50:
        await message.answer("Напишіть коротко, наприклад: протягом години")
        return
    await state.update_data(adm_term=term)
    await _finish(message, state, bot)


@router.message(StateFilter(AdminAnswer))
async def wrong_input(message: Message) -> None:
    await message.answer("Напишіть відповідь текстом або натисніть «Скасувати відповідь».")


# --- Итог: таблица → клиент → убрать кнопки → отчёт админу ---
async def _finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    number, action = data["adm_number"], data["adm_action"]
    price, term = data.get("adm_price", ""), data.get("adm_term", "")

    request = await sheets.get_request(number)
    if request is None:
        await message.answer(f"❌ Заявку №{number} не знайдено в таблиці.", reply_markup=NO_KB)
        return

    try:
        await sheets.update_answer(number, STATUS_BY_ACTION[action], price, term)
    except Exception:
        logging.exception("Не удалось обновить заявку в таблице")
        await message.answer("❌ Не вдалося записати в таблицю, спробуйте ще раз.", reply_markup=NO_KB)
        return

    report = f"✅ Відповідь на заявку №{number} надіслано клієнту."
    try:
        await bot.send_message(int(request["tg_id"]), _client_text(action, number, request, price, term))
    except (TelegramForbiddenError, TelegramBadRequest, ValueError):
        # Клиент заблокировал бота или ID в таблице испорчен — пусть папа позвонит
        logging.warning("Не удалось отправить ответ клиенту по заявке %s", number)
        report = (f"⚠️ Клієнт заблокував бота, повідомлення не дійшло.\n"
                  f"Зателефонуйте: <b>{html.escape(request['phone'])}</b> "
                  f"({html.escape(request['client'])})\nУ таблиці статус оновлено.")

    await _remove_buttons(bot, data["adm_chat"], data["adm_msg"])
    await message.answer(report, reply_markup=NO_KB)


def _client_text(action: str, number: int, request: dict, price: str, term: str) -> str:
    part = html.escape(request["part"])
    if action == "stock":
        return (f"✅ Гарна новина! За заявкою №{number} (<b>{part}</b>) деталь є в наявності.\n"
                f"💰 Ціна: <b>{price}</b>\n\n"
                f"Приїжджайте або телефонуйте:\n{SHOP_CONTACTS}")
    if action == "order":
        return (f"📦 За заявкою №{number} (<b>{part}</b>) деталь можна замовити.\n"
                f"💰 Ціна: <b>{price}</b>\n🚚 Буде: <b>{html.escape(term)}</b>\n\n"
                f"Ми зателефонуємо вам для підтвердження замовлення.\n\n{SHOP_CONTACTS}")
    return (f"😔 На жаль, за заявкою №{number} (<b>{part}</b>) деталі зараз немає.\n"
            f"Вибачте за незручності! Можете залишити заявку на іншу запчастину "
            f"або зателефонувати нам:\n\n{SHOP_CONTACTS}")


async def _remove_buttons(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest:
        pass  # кнопок уже нет или сообщение слишком старое — не страшно

"""
Подпись «хто це» о клиенте: папа (или коллега) пишет для себя, кто заказывает,
и в следующих заявках этого клиента видит подпись.
Подпись показывается ТОЛЬКО сотрудникам — клиенту бот её никогда не отправляет.
Хранится на листе «Клієнти» в той же Google Таблице (sheets.get_note / set_note).
"""
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

import settings
import sheets
from keyboards import BTN_ADMIN_CANCEL, BTN_NOTE_DELETE, NoteCb

router = Router()
router.message.filter(F.from_user.id.func(settings.is_staff))
router.callback_query.filter(F.from_user.id.func(settings.is_staff))

NOTE_MAX = 200


class NoteInput(StatesGroup):
    text = State()


def note_line(note: str) -> str:
    """Строка для сообщений сотрудникам (пусто, если подписи нет)."""
    return f"🏷 <b>Хто це:</b> <i>{html.escape(note)}</i>\n" if note else ""


async def safe_get_note(tg_id) -> str:
    """Подпись или "" — сбой таблицы не должен ломать уведомление о заявке."""
    try:
        return await sheets.get_note(tg_id)
    except Exception:
        logging.exception("Не удалось прочитать подпись клиента")
        return ""


@router.callback_query(NoteCb.filter())
async def on_note_button(callback: CallbackQuery, callback_data: NoteCb, state: FSMContext) -> None:
    number = callback_data.number
    if not settings.can_handle(callback.from_user.id, number):
        await callback.answer("Ця заявка вам не передана", show_alert=True)
        return
    request = await sheets.get_request(number)
    if request is None or not request["tg_id"].strip():
        await callback.answer(f"Заявку №{number} не знайдено в таблиці", show_alert=True)
        return

    await callback.answer()
    note = await safe_get_note(request["tg_id"])
    await state.clear()
    await state.set_state(NoteInput.text)
    await state.update_data(note_tg=request["tg_id"], note_client=request["client"],
                            note_phone=request["phone"])
    rows = [[KeyboardButton(text=BTN_ADMIN_CANCEL)]]
    if note:
        rows.insert(0, [KeyboardButton(text=BTN_NOTE_DELETE)])
    await callback.message.answer(
        f"📝 Клієнт із заявки №{number}: <b>{html.escape(request['client'])}</b>, "
        f"{html.escape(request['phone'])}\n"
        + (f"Зараз підписаний так: <i>{html.escape(note)}</i>\n" if note else "")
        + "\nНапишіть, хто це — наприклад: <i>сусід Іван, бере на Ланос</i>.\n"
        "🔒 Підпис бачите лише ви і колега, клієнт його не бачить.",
        reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True),
    )


@router.message(StateFilter(NoteInput), F.text == BTN_ADMIN_CANCEL)
@router.message(StateFilter(NoteInput), Command("cancel"))
async def cancel_note(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=ReplyKeyboardRemove())


@router.message(NoteInput.text, F.text)
async def save_note(message: Message, state: FSMContext) -> None:
    note = "" if message.text == BTN_NOTE_DELETE else message.text.strip()
    if len(note) > NOTE_MAX:
        await message.answer(f"Задовго — напишіть коротше (до {NOTE_MAX} символів).")
        return
    data = await state.get_data()
    try:
        await sheets.set_note(data["note_tg"], data["note_client"], data["note_phone"], note)
    except Exception:
        logging.exception("Не удалось сохранить подпись клиента")
        await message.answer("❌ Не вдалося зберегти, спробуйте ще раз.")
        return
    await state.clear()
    if note:
        text = (f"✅ Збережено. У наступних заявках цього клієнта буде:\n"
                f"🏷 <b>Хто це:</b> <i>{html.escape(note)}</i>")
    else:
        text = "🗑 Підпис видалено."
    await message.answer(text, reply_markup=ReplyKeyboardRemove())


@router.message(StateFilter(NoteInput))
async def wrong_note(message: Message) -> None:
    await message.answer("Напишіть підпис текстом або натисніть «Скасувати відповідь».")

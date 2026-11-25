"""
Форма заявки на запчасть (FSM — машина состояний).
Клиент по шагам отвечает на вопросы, бот запоминает ответы в state,
в конце показывает итог и после подтверждения пишет заявку в таблицу.
"""
import html
import logging
import re
from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import config
import notes
import settings
import sheets
from keyboards import (
    BTN_CANCEL, BTN_OTHER_CAR, BTN_REQUEST, BTN_RESTART, BTN_SAME_CAR, BTN_SEND, BTN_VIN_SKIP,
    CANCEL_KB, CONFIRM_KB, MAIN_MENU, SAME_CAR_KB, VIN_KB, admin_answer_kb, note_only_kb, phone_kb,
)

router = Router()

# VIN: 17 символов, латиница и цифры, без I, O, Q
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{8,18}$")


class RequestForm(StatesGroup):
    same_car = State()  # «Те саме авто?» — если клиент уже оставлял заявку
    car = State()
    year = State()
    vin = State()
    part = State()
    phone = State()
    confirm = State()


# --- Отмена работает на любом шаге, поэтому этот обработчик стоит первым ---
@router.message(StateFilter(RequestForm), F.text == BTN_CANCEL)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заявку скасовано.", reply_markup=MAIN_MENU)


# --- Старт формы ---
@router.message(F.text.in_({BTN_REQUEST, BTN_RESTART}))
async def start_form(message: Message, state: FSMContext) -> None:
    await state.clear()
    # «Память» — последняя заявка клиента в таблице (переживает перезапуск бота)
    try:
        previous = await sheets.client_requests(message.from_user.id, limit=1)
    except Exception:
        logging.exception("Не удалось прочитать прошлые заявки — идём по обычной форме")
        previous = []

    if previous:
        last = previous[-1]
        vin = last["vin"]
        if vin == "фото техпаспорта":
            vin = f"фото техпаспорта (у заявці №{last['number']})"  # само фото есть у админа
        await state.update_data(prev={
            "car": last["car"], "year": last["year"], "vin": vin, "phone": last["phone"],
        })
        await state.set_state(RequestForm.same_car)
        await message.answer(
            "Минулого разу ви шукали для:\n"
            f"🚗 <b>{html.escape(last['car'])}, {html.escape(last['year'])}</b>\n"
            f"🔢 VIN: {html.escape(vin)}\n\n"
            "Шукаємо для того самого авто?",
            reply_markup=SAME_CAR_KB,
        )
        return
    await _ask_car(message, state)


async def _ask_car(message: Message, state: FSMContext) -> None:
    await state.set_state(RequestForm.car)
    await message.answer(
        "1/5. Марка і модель авто?\nНаприклад: <b>VW Golf 6</b>",
        reply_markup=CANCEL_KB,
    )


@router.message(RequestForm.same_car, F.text == BTN_SAME_CAR)
async def same_car(message: Message, state: FSMContext) -> None:
    prev = (await state.get_data())["prev"]
    # reuse=True — после запчасти сразу к подтверждению, телефон уже знаем
    await state.update_data(car=prev["car"], year=prev["year"], vin=prev["vin"], reuse=True)
    await state.set_state(RequestForm.part)
    await message.answer(
        "Яка запчастина потрібна?\nНаприклад: <b>передні гальмівні колодки</b>",
        reply_markup=CANCEL_KB,
    )


@router.message(RequestForm.same_car, F.text == BTN_OTHER_CAR)
async def other_car(message: Message, state: FSMContext) -> None:
    await _ask_car(message, state)


@router.message(RequestForm.same_car)
async def same_car_wrong(message: Message) -> None:
    await message.answer("Оберіть кнопку нижче: «Те саме авто» або «Інше авто».",
                         reply_markup=SAME_CAR_KB)


@router.message(RequestForm.car, F.text)
async def step_car(message: Message, state: FSMContext) -> None:
    car = message.text.strip()
    if not 2 <= len(car) <= 60:
        await message.answer("Напишіть, будь ласка, марку і модель, наприклад: VW Golf 6")
        return
    await state.update_data(car=car)
    await state.set_state(RequestForm.year)
    await message.answer("2/5. Рік випуску? Наприклад: <b>2010</b>")


@router.message(RequestForm.year, F.text)
async def step_year(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not (text.isdigit() and 1960 <= int(text) <= date.today().year + 1):
        await message.answer("Вкажіть рік чотирма цифрами, наприклад: 2010")
        return
    await state.update_data(year=text)
    await state.set_state(RequestForm.vin)
    await message.answer(
        "3/5. VIN-код (17 символів) — щоб точно підібрати деталь.\n"
        "Можна надіслати <b>фото техпаспорта</b> або натиснути «Не знаю VIN».",
        reply_markup=VIN_KB,
    )


@router.message(RequestForm.vin, F.photo)
async def step_vin_photo(message: Message, state: FSMContext) -> None:
    # Берём самое большое фото; в таблицу пишем пометку, само фото уйдёт админу
    await state.update_data(vin="фото техпаспорта", photo_id=message.photo[-1].file_id)
    await _ask_part(message, state)


@router.message(RequestForm.vin, F.text)
async def step_vin_text(message: Message, state: FSMContext) -> None:
    if message.text == BTN_VIN_SKIP:
        await state.update_data(vin="—")
    else:
        vin = re.sub(r"[\s\-]", "", message.text).upper()
        if not VIN_RE.match(vin):
            await message.answer(
                "VIN має 17 символів (латиниця і цифри, без I, O, Q).\n"
                "Перевірте, надішліть фото техпаспорта або натисніть «Не знаю VIN»."
            )
            return
        await state.update_data(vin=vin)
    await _ask_part(message, state)


async def _ask_part(message: Message, state: FSMContext) -> None:
    await state.set_state(RequestForm.part)
    await message.answer(
        "4/5. Яка запчастина потрібна?\nНаприклад: <b>передні гальмівні колодки</b>",
        reply_markup=CANCEL_KB,
    )


@router.message(RequestForm.part, F.text)
async def step_part(message: Message, state: FSMContext) -> None:
    part = message.text.strip()
    if not 2 <= len(part) <= 500:
        await message.answer("Опишіть, будь ласка, що саме потрібно.")
        return
    await state.update_data(part=part)
    data = await state.get_data()
    prev_phone = data.get("prev", {}).get("phone", "")
    if data.get("reuse") and prev_phone:
        await _show_summary(message, state, prev_phone)  # то же авто — телефон уже знаем
        return
    await state.set_state(RequestForm.phone)
    await message.answer(
        "5/5. Ваш номер телефону — щоб ми могли зв'язатися.\n"
        "Натисніть кнопку нижче або напишіть номер.",
        reply_markup=phone_kb(prev_phone),
    )


@router.message(RequestForm.phone, F.contact)
async def step_phone_contact(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await _show_summary(message, state, phone)


@router.message(RequestForm.phone, F.text)
async def step_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text.strip()
    if not PHONE_RE.match(phone):
        await message.answer("Не схоже на номер. Приклад: +380 67 123 45 67")
        return
    await _show_summary(message, state, phone)


def _summary(data: dict) -> str:
    # html.escape — чтобы символы < > & в ответах клиента не ломали HTML-разметку
    e = {key: html.escape(str(value)) for key, value in data.items()}
    return (
        f"🚗 <b>Авто:</b> {e['car']}, {e['year']}\n"
        f"🔢 <b>VIN:</b> {e['vin']}\n"
        f"🔧 <b>Потрібно:</b> {e['part']}\n"
        f"📞 <b>Телефон:</b> {e['phone']}"
    )


def normalize_phone(phone: str) -> str:
    """Приводим номер к виду +380671234567: номер с пробелами Telegram
    не делает нажимаемым, а папе нужно звонить в одно касание."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10 and digits.startswith("0"):  # 067... → +38067...
        digits = "38" + digits
    return "+" + digits


async def _show_summary(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=normalize_phone(phone))
    await state.set_state(RequestForm.confirm)
    data = await state.get_data()
    await message.answer(
        "Перевірте заявку:\n\n" + _summary(data),
        reply_markup=CONFIRM_KB,
    )


@router.message(RequestForm.confirm, F.text == BTN_SEND)
async def send_request(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    data["client"] = message.from_user.full_name
    data["tg_id"] = message.from_user.id
    data["username"] = message.from_user.username

    try:
        number = await sheets.append_request(data)
    except Exception:
        logging.exception("Не удалось записать заявку в таблицу")
        await message.answer("😔 Сталася помилка, спробуйте ще раз трохи пізніше.")
        return

    await state.clear()
    await message.answer(
        f"✅ Заявку №{number} прийнято! Ми вам зателефонуємо найближчим часом.",
        reply_markup=MAIN_MENU,
    )
    await _notify_admin(bot, number, data)


async def _notify_admin(bot: Bot, number: int, data: dict) -> None:
    """Сообщение админу (папе). Ошибка здесь не должна ломать заявку клиента."""
    admin_id = settings.admin_id()  # после передачи бота — новый админ (папа)
    if not admin_id:
        return
    username = f" (@{data.get('username')})" if data.get("username") else ""
    # Подпись «хто це» — только в сообщении сотруднику, клиенту она не уходит никогда
    note = await notes.safe_get_note(data["tg_id"])
    text = (f"🆕 <b>Нова заявка №{number}</b>\n👤 {html.escape(data['client'])}{username}\n"
            + notes.note_line(note) + "\n" + _summary(data))
    if config.ADMIN_REPLIES:
        # Кнопки ответа (admin.py) + «Надіслати: <колега>» + «Підписати клієнта»
        keyboard = admin_answer_kb(number, settings.colleagues())
        text += "\n\n👇 <b>Оберіть відповідь клієнту</b> або зателефонуйте — натисніть на номер."
    else:
        # Простой режим: папа просто звонит. Номер Telegram сам делает нажимаемым.
        keyboard = note_only_kb(number)
        text += "\n\n☎️ <b>Зателефонуйте клієнту</b> — натисніть на номер вище."
    try:
        if data.get("photo_id"):
            await bot.send_photo(admin_id, data["photo_id"], caption=text, reply_markup=keyboard)
        else:
            await bot.send_message(admin_id, text, reply_markup=keyboard)
    except Exception:
        logging.exception("Не удалось отправить уведомление админу")


# --- Если клиент прислал что-то не то (стикер, голосовое и т.п.) ---
@router.message(StateFilter(RequestForm))
async def wrong_input(message: Message) -> None:
    await message.answer("Будь ласка, дайте відповідь текстом або кнопкою нижче.")

"""
Ответ магазина на заявку (режим ADMIN_REPLIES=1).

Сотрудники — админ (папа) и коллеги (например, Олег):
- под уведомлением кнопки «Є» / «Під замовлення» / «Немає», у админа ещё «Надіслати: <колега>»;
- коллега получает заявку, только если админ нажал «Надіслати», и отвечает так же;
- «Під замовлення»: клиент жмёт «Беру» / «Не потрібно» → сотрудникам приходит итог,
  при «Беру» — кнопка «Привезли», по ней клиенту уходит «можна забирати».
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
from aiogram.utils.deep_linking import create_start_link

import settings
import sheets
from keyboards import (
    ADMIN_CANCEL_KB, BTN_ADMIN_CANCEL, TERM_KB, AdminCb, ClientCb, StaffCb,
    admin_answer_kb, arrived_kb, client_decision_kb, staff_manage_kb,
)
from texts import (
    SHOP_CONTACTS, STATUS_ARRIVED, STATUS_CONFIRMED, STATUS_DECLINED, STATUS_IN_STOCK,
    STATUS_NEW, STATUS_NONE, STATUS_ORDER,
)

# Сотрудники: чужие нажатия и сообщения сюда не попадут
router = Router()
router.message.filter(F.from_user.id.func(settings.is_staff))
router.callback_query.filter(F.from_user.id.func(settings.is_staff))

# Клиентские кнопки «Беру / Не потрібно» — их жмут клиенты, фильтра по сотрудникам нет
client_router = Router()

# Сотрудникам клиентское меню не нужно — после ответа просто убираем клавиатуру
NO_KB = ReplyKeyboardRemove()

STATUS_BY_ACTION = {"stock": STATUS_IN_STOCK, "order": STATUS_ORDER, "none": STATUS_NONE}

# Цена: «1200», «1 200», «1200 грн», «1200,50»
PRICE_RE = re.compile(r"^\d{1,7}([.,]\d{1,2})?$")


class AdminAnswer(StatesGroup):
    price = State()
    term = State()


# ============ Кнопки под заявкой ============

@router.callback_query(AdminCb.filter(F.action == "fwd"))
async def on_forward(callback: CallbackQuery, callback_data: AdminCb, bot: Bot) -> None:
    """Админ передаёт заявку коллеге."""
    number, colleague = callback_data.number, callback_data.to
    if not settings.is_admin(callback.from_user.id):
        await callback.answer("Передавати заявки може лише адмін", show_alert=True)
        return
    name = settings.colleagues().get(colleague)
    if name is None:
        await callback.answer("Цього колеги вже немає в списку", show_alert=True)
        return

    original = callback.message
    text = "📨 <b>Вам передали заявку</b>\n\n" + (original.html_text or "")
    try:
        if original.photo:
            await bot.send_photo(colleague, original.photo[-1].file_id, caption=text,
                                 reply_markup=admin_answer_kb(number))
        else:
            await bot.send_message(colleague, text, reply_markup=admin_answer_kb(number))
    except (TelegramForbiddenError, TelegramBadRequest):
        await callback.answer(f"Не вдалося надіслати: {name} не відкрив бота або заблокував його",
                              show_alert=True)
        return

    settings.assign(number, colleague)
    await callback.answer(f"Надіслано: {name}")
    # У админа убираем кнопку пересылки и помечаем, кому передано; ответить сам он всё ещё может
    note = f"\n\n➡️ <b>Передано: {html.escape(name)}</b>"
    try:
        if original.photo:
            await bot.edit_message_caption(chat_id=original.chat.id, message_id=original.message_id,
                                           caption=(original.html_text or "") + note,
                                           reply_markup=admin_answer_kb(number))
        else:
            await bot.edit_message_text(chat_id=original.chat.id, message_id=original.message_id,
                                        text=(original.html_text or "") + note,
                                        reply_markup=admin_answer_kb(number))
    except TelegramBadRequest:
        pass


@router.callback_query(AdminCb.filter(F.action == "arrived"))
async def on_arrived(callback: CallbackQuery, callback_data: AdminCb, bot: Bot) -> None:
    """Деталь под заказ привезли — сообщаем клиенту, что можно забирать."""
    number = callback_data.number
    if not settings.can_handle(callback.from_user.id, number):
        await callback.answer("Ця заявка вам не передана", show_alert=True)
        return
    request = await sheets.get_request(number)
    if request is None or request["status"] != STATUS_CONFIRMED:
        status = request["status"] if request else "не знайдено"
        await callback.answer(f"Заявка №{number}: {status}. Клієнту вже не потрібно писати.",
                              show_alert=True)
        await _remove_buttons(bot, callback.message.chat.id, callback.message.message_id)
        return

    await sheets.update_status(number, STATUS_ARRIVED)
    await callback.answer()
    await _remove_buttons(bot, callback.message.chat.id, callback.message.message_id)
    part = html.escape(request["part"])
    sent = await _send_client(bot, request, (
        f"✅ Деталь за заявкою №{number} (<b>{part}</b>) вже в магазині — можна забирати!\n"
        f"💰 Ціна: <b>{html.escape(request['price'])}</b>\n\n{SHOP_CONTACTS}"
    ))
    who = settings.staff_name(callback.from_user.id)
    report = (f"✅ Клієнту по заявці №{number} написали, що деталь можна забирати." if sent
              else _blocked_text(request))
    await callback.message.answer(report)
    await _notify_other_staff(bot, number, callback.from_user.id,
                              f"✅ {html.escape(who)}: деталь по заявці №{number} привезли, клієнту написали.")


@router.callback_query(AdminCb.filter())
async def on_answer_button(callback: CallbackQuery, callback_data: AdminCb,
                           state: FSMContext, bot: Bot) -> None:
    """«Є» / «Під замовлення» / «Немає»."""
    number = callback_data.number
    if not settings.can_handle(callback.from_user.id, number):
        await callback.answer("Ця заявка вам не передана", show_alert=True)
        return
    request = await sheets.get_request(number)
    if request is None:
        await callback.answer(f"Заявку №{number} не знайдено в таблиці", show_alert=True)
        return
    if request["status"] not in ("", STATUS_NEW):
        # Уже ответили (кнопкой, коллега или вручную в таблице) — не шлём клиенту второй раз
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
        await _finish(callback.message, state, bot, callback.from_user.id)
        return

    await state.set_state(AdminAnswer.price)
    await callback.message.answer(
        f"Заявка №{number}: <b>{html.escape(request['part'])}</b>\n"
        "💰 Напишіть ціну в гривнях, наприклад: <b>1200</b>",
        reply_markup=ADMIN_CANCEL_KB,
    )


# ============ Ввод цены и срока ============

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
        await _finish(message, state, bot, message.from_user.id)
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
    await _finish(message, state, bot, message.from_user.id)


@router.message(StateFilter(AdminAnswer))
async def wrong_input(message: Message) -> None:
    await message.answer("Напишіть відповідь текстом або натисніть «Скасувати відповідь».")


# --- Итог: таблица → клиент → убрать кнопки → отчёт ---
async def _finish(message: Message, state: FSMContext, bot: Bot, staff_id: int) -> None:
    data = await state.get_data()
    await state.clear()
    number, action = data["adm_number"], data["adm_action"]
    price, term = data.get("adm_price", ""), data.get("adm_term", "")

    request = await sheets.get_request(number)
    if request is None:
        await message.answer(f"❌ Заявку №{number} не знайдено в таблиці.", reply_markup=NO_KB)
        return
    if request["status"] not in ("", STATUS_NEW):
        # Пока вводили цену, ответил кто-то другой
        await message.answer(f"На заявку №{number} вже відповіли: {request['status']}.", reply_markup=NO_KB)
        await _remove_buttons(bot, data["adm_chat"], data["adm_msg"])
        return

    try:
        await sheets.update_answer(number, STATUS_BY_ACTION[action], price, term)
        if settings.is_colleague(staff_id):
            await sheets.update_status(number, STATUS_BY_ACTION[action],
                                       comment=f"Відповів: {settings.staff_name(staff_id)}")
    except Exception:
        logging.exception("Не удалось обновить заявку в таблице")
        await message.answer("❌ Не вдалося записати в таблицю, спробуйте ще раз.", reply_markup=NO_KB)
        return

    keyboard = client_decision_kb(number) if action == "order" else None
    sent = await _send_client(bot, request, _client_text(action, number, request, price, term), keyboard)
    report = f"✅ Відповідь на заявку №{number} надіслано клієнту." if sent else _blocked_text(request)
    if sent and action == "order":
        report += "\nКоли клієнт натисне «Беру» або «Не потрібно» — бот вам напише."

    await _remove_buttons(bot, data["adm_chat"], data["adm_msg"])
    await message.answer(report, reply_markup=NO_KB)

    summary = {"stock": f"✅ Є в наявності, {price}",
               "order": f"📦 Під замовлення, {price}, буде: {html.escape(term)}",
               "none": "❌ Немає"}[action]
    await _notify_other_staff(bot, number, staff_id,
                              f"👨‍🔧 {html.escape(settings.staff_name(staff_id))} відповів(ла) "
                              f"на заявку №{number}: {summary}")


def _client_text(action: str, number: int, request: dict, price: str, term: str) -> str:
    part = html.escape(request["part"])
    if action == "stock":
        return (f"✅ Гарна новина! За заявкою №{number} (<b>{part}</b>) деталь є в наявності.\n"
                f"💰 Ціна: <b>{price}</b>\n\n"
                f"Приїжджайте або телефонуйте:\n{SHOP_CONTACTS}")
    if action == "order":
        return (f"📦 За заявкою №{number} (<b>{part}</b>) деталь можна замовити.\n"
                f"💰 Ціна: <b>{price}</b>\n🚚 Буде: <b>{html.escape(term)}</b>\n\n"
                "Замовляємо? Натисніть кнопку нижче 👇")
    return (f"😔 На жаль, за заявкою №{number} (<b>{part}</b>) деталі зараз немає.\n"
            f"Вибачте за незручності! Можете залишити заявку на іншу запчастину "
            f"або зателефонувати нам:\n\n{SHOP_CONTACTS}")


# ============ Клиент: «Беру» / «Не потрібно» ============

@client_router.callback_query(ClientCb.filter())
async def on_client_decision(callback: CallbackQuery, callback_data: ClientCb, bot: Bot) -> None:
    number = callback_data.number
    request = await sheets.get_request(number)
    if request is None or request["tg_id"].strip() != str(callback.from_user.id):
        await callback.answer("Заявку не знайдено", show_alert=True)
        return
    await _remove_buttons(bot, callback.message.chat.id, callback.message.message_id)
    if request["status"] != STATUS_ORDER:
        await callback.answer("Відповідь по цій заявці вже отримано 👍", show_alert=True)
        return

    await callback.answer()
    part = html.escape(request["part"])
    phone = html.escape(request["phone"])
    if callback_data.action == "yes":
        await sheets.update_status(number, STATUS_CONFIRMED)
        await callback.message.answer(
            "👍 Дякуємо! Замовляємо деталь. Щойно вона буде в магазині — бот одразу вам напише.")
        text = (f"✅ <b>Клієнт бере — замовте деталь</b> (заявка №{number})\n"
                f"🔧 {part}\n💰 {html.escape(request['price'])}, 🚚 {html.escape(request['term'])}\n"
                f"📞 {phone}\n\n"
                "Коли деталь привезуть — натисніть кнопку нижче, клієнт отримає повідомлення 👇")
        keyboard = arrived_kb(number)
    else:
        await sheets.update_status(number, STATUS_DECLINED)
        await callback.message.answer(
            "Зрозуміли, не замовляємо. Якщо знадобиться інша запчастина — "
            "натисніть «🔧 Підібрати запчастину».")
        text = (f"❌ <b>Клієнт відмовився</b> від заявки №{number} — замовляти не потрібно.\n"
                f"🔧 {part}\n📞 {phone}")
        keyboard = None

    for staff_id in settings.staff_for(number):
        try:
            await bot.send_message(staff_id, text, reply_markup=keyboard)
        except (TelegramForbiddenError, TelegramBadRequest):
            logging.warning("Не удалось сообщить сотруднику %s о решении клиента", staff_id)


# ============ Колеги (кнопки у админа на /start) ============

async def send_staff_panel(message: Message) -> None:
    """Панель «Колеги» для админа: добавить / убрать."""
    colleagues = settings.colleagues()
    names = ", ".join(html.escape(n) for n in colleagues.values()) or "поки немає"
    await message.answer(
        f"👥 <b>Колеги:</b> {names}\n"
        "Колега отримує заявку, лише коли ви натиснете під нею «👨‍🔧 Надіслати: …».",
        reply_markup=staff_manage_kb(colleagues),
    )


@router.callback_query(StaffCb.filter())
async def on_staff_manage(callback: CallbackQuery, callback_data: StaffCb, bot: Bot) -> None:
    if not settings.is_admin(callback.from_user.id):
        await callback.answer("Лише для адміна", show_alert=True)
        return
    await callback.answer()
    if callback_data.action == "add":
        link = await create_start_link(bot, "staff_" + settings.new_colleague_token())
        await callback.message.answer(
            "🔗 Перешліть це посилання колезі (одноразове, діє 7 днів):\n"
            f"{link}\n\n"
            "Коли колега відкриє його і натисне «Start», під новими заявками з’явиться "
            "кнопка «👨‍🔧 Надіслати: …» з його ім’ям.")
        return
    name = settings.remove_colleague(callback_data.user)
    if name:
        await callback.message.answer(f"Колегу {html.escape(name)} прибрано. Заявки йому більше не надходять.")
        try:
            await bot.send_message(callback_data.user, "Вас прибрали зі списку колег АвтоХати.")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
    await send_staff_panel(callback.message)


# ============ Вспомогательное ============

async def _send_client(bot: Bot, request: dict, text: str, keyboard=None) -> bool:
    """False — клиент заблокировал бота (или ID в таблице испорчен)."""
    try:
        await bot.send_message(int(request["tg_id"]), text, reply_markup=keyboard)
        return True
    except (TelegramForbiddenError, TelegramBadRequest, ValueError):
        logging.warning("Не удалось написать клиенту по заявке %s", request["number"])
        return False


def _blocked_text(request: dict) -> str:
    return (f"⚠️ Клієнт заблокував бота, повідомлення не дійшло.\n"
            f"Зателефонуйте: <b>{html.escape(request['phone'])}</b> "
            f"({html.escape(request['client'])})\nУ таблиці статус оновлено.")


async def _notify_other_staff(bot: Bot, number: int, actor_id: int, text: str) -> None:
    """Сообщить остальным сотрудникам по заявке (например, папе — что ответил Олег)."""
    for staff_id in settings.staff_for(number):
        if staff_id == actor_id:
            continue
        try:
            await bot.send_message(staff_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass


async def _remove_buttons(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest:
        pass  # кнопок уже нет или сообщение слишком старое — не страшно

"""
Бот заявок на запчасти для магазина АвтоХата.
Форма заявки (form.py), ответ админа (admin.py), «Мої заявки», запись в Google Таблицу.
"""
import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.utils.deep_linking import create_start_link

import admin
import config
import form
import notes
import settings
import sheets
from keyboards import BTN_CONTACTS, BTN_MY_REQUESTS, MAIN_MENU
from texts import SHOP_CONTACTS, STATUS_FOR_CLIENT, STATUS_NEW

dp = Dispatcher()


def admin_greeting() -> str:
    text = "👋 Вітаю! Сюди приходитимуть нові заявки від клієнтів.\n"
    if config.ADMIN_REPLIES:
        text += ("Під кожною заявкою будуть кнопки: <b>✅ Є в наявності</b>, "
                 "<b>📦 Під замовлення</b> або <b>❌ Немає</b> — бот сам напише клієнту.\n")
    return text + "Щоб зателефонувати клієнту — натисніть на його номер у заявці."


# --- Передача бота: одноразовая ссылка t.me/<бот>?start=admin_<код> ---
@dp.message(CommandStart(deep_link=True, magic=F.args.startswith("admin_")))
async def cmd_start_handover(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    user = message.from_user
    previous = settings.use_handover_token(message.text.split("admin_", 1)[1], user.id)
    if previous is None:
        await message.answer("Посилання вже використане або застаріло.", reply_markup=MAIN_MENU)
        return
    await send_admin_start(message)
    if previous and previous != user.id:
        try:
            await bot.send_message(previous, f"✅ Бот передано: {html.escape(user.full_name)}. "
                                             "Тепер заявки приходять йому.")
        except Exception:
            logging.warning("Не удалось предупредить прежнего админа")


# --- Приглашение коллеги: одноразовая ссылка t.me/<бот>?start=staff_<код> ---
@dp.message(CommandStart(deep_link=True, magic=F.args.startswith("staff_")))
async def cmd_start_colleague(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    user = message.from_user
    if not settings.use_colleague_token(message.text.split("staff_", 1)[1], user.id, user.first_name):
        await message.answer("Посилання вже використане або застаріло.", reply_markup=MAIN_MENU)
        return
    await message.answer(COLLEAGUE_GREETING, reply_markup=ReplyKeyboardRemove())
    try:
        await bot.send_message(
            settings.admin_id(),
            f"👨‍🔧 <b>{html.escape(user.first_name)}</b> тепер колега. Під новими заявками з’явиться "
            f"кнопка «👨‍🔧 Надіслати: {html.escape(user.first_name)}».")
    except Exception:
        logging.warning("Не удалось сообщить админу о новом коллеге")


COLLEAGUE_GREETING = (
    "👋 Вітаю! Ви колега в АвтоХаті.\n"
    "Сюди приходитимуть заявки, які вам передасть адмін. Під заявкою — кнопки "
    "<b>✅ Є в наявності</b>, <b>📦 Під замовлення</b> або <b>❌ Немає</b>: бот сам напише клієнту, "
    "а адмін побачить вашу відповідь.\n"
    "Щоб зателефонувати клієнту — натисніть на його номер у заявці."
)


async def send_admin_start(message: Message) -> None:
    # Папе клиентское меню не нужно — только заявки. Коллег он сам не добавляет:
    # ссылку-приглашение делаем на сервере (settings.new_colleague_token), см. CLAUDE.md.
    # Скрытая команда /kolegy — панель коллег на всякий случай.
    await message.answer(admin_greeting(), reply_markup=ReplyKeyboardRemove())


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # если клиент застрял в форме — начинаем с чистого листа
    if settings.is_admin(message.from_user.id):
        await send_admin_start(message)
        return
    if config.ADMIN_REPLIES and settings.is_colleague(message.from_user.id):
        await message.answer(COLLEAGUE_GREETING, reply_markup=ReplyKeyboardRemove())
        return
    await show_client_menu(message)


@dp.message(Command("kolegy"), F.from_user.id.func(settings.is_admin))
async def cmd_colleagues(message: Message) -> None:
    await admin.send_staff_panel(message)


@dp.message(Command("peredat"), F.from_user.id.func(settings.is_admin))
async def cmd_handover(message: Message, bot: Bot) -> None:
    # Новый админ открывает ссылку — и бот переходит к нему. Ссылка одноразовая.
    link = await create_start_link(bot, "admin_" + settings.new_handover_token())
    await message.answer(
        "🔑 Посилання для передачі бота (одноразове, діє 7 днів):\n"
        f"{link}\n\n"
        "Коли нова людина відкриє його і натисне «Start», заявки почнуть приходити їй, "
        "а не вам."
    )


@dp.message(Command("client"))
async def show_client_menu(message: Message) -> None:
    # /client — скрытая команда: админ может посмотреть бота глазами клиента (для теста)
    await message.answer(
        "Вітаємо в АвтоХаті! 🚗\n"
        "Допоможемо знайти потрібну запчастину для вашого авто.\n"
        "Натисніть «🔧 Підібрати запчастину», щоб залишити заявку.",
        reply_markup=MAIN_MENU,
    )


@dp.message(Command("id"))
async def cmd_id(message: Message) -> None:
    # Пригодится, чтобы узнать Telegram ID папы и прописать его админом
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(F.text == BTN_CONTACTS)
async def contacts(message: Message) -> None:
    await message.answer(SHOP_CONTACTS)


@dp.message(F.text == BTN_MY_REQUESTS, F.func(lambda _: config.ADMIN_REPLIES))
async def my_requests(message: Message) -> None:
    # Читаем прямо из таблицы: если папа поменял статус вручную, клиент увидит актуальное
    try:
        requests = await sheets.client_requests(message.from_user.id, limit=5)
    except Exception:
        logging.exception("Не удалось прочитать заявки клиента")
        await message.answer("😔 Сталася помилка, спробуйте трохи пізніше.")
        return
    if not requests:
        await message.answer("У вас ще немає заявок. Натисніть «🔧 Підібрати запчастину».")
        return

    blocks = []
    for r in reversed(requests):  # новые сверху
        status = STATUS_FOR_CLIENT.get(r["status"], r["status"] or STATUS_FOR_CLIENT[STATUS_NEW])
        lines = [
            f"<b>№{r['number']}</b> від {r['date'][:10]} — {html.escape(status)}",
            f"🚗 {html.escape(r['car'])}, {html.escape(r['year'])}",
            f"🔧 {html.escape(r['part'])}",
        ]
        if r["price"]:
            lines.append(f"💰 {html.escape(r['price'])}")
        if r["term"]:
            lines.append(f"🚚 {html.escape(r['term'])}")
        blocks.append("\n".join(lines))
    await message.answer("📋 <b>Ваші останні заявки:</b>\n\n" + "\n\n".join(blocks))


@dp.message(Command("test"))
async def cmd_test(message: Message) -> None:
    # Проверка связи с таблицей — только админу (если админ не задан — можно всем, для настройки)
    if settings.admin_id() and not settings.is_admin(message.from_user.id):
        return
    await message.answer("Пишу тестовий рядок у таблицю...")
    try:
        number = await sheets.append_test_row()
        await message.answer(f"✅ Готово, рядок №{number} у таблиці")
    except Exception as error:  # покажем ошибку, чтобы было понятно, что чинить
        logging.exception("Ошибка Google Sheets")
        await message.answer(f"❌ Помилка: {type(error).__name__}: {error}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if config.DEMO:
        logging.warning("ДЕМО-РЕЖИМ: тестовый бот, файл %s", config.ENV_FILE.name)
    # HTML-разметка по умолчанию: <b>жирный</b>, <code>код</code>
    # link_preview_is_disabled — без большой карточки Google Maps под контактами
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    # admin — раньше form: пока папа вводит цену, его текст не должен попасть в форму.
    # В простом режиме (ADMIN_REPLIES=0) ответы кнопками не подключаем вовсе.
    dp.include_router(notes.router)  # «Підписати клієнта» — в обоих режимах
    if config.ADMIN_REPLIES:
        dp.include_router(admin.router)
        dp.include_router(admin.client_router)  # «Беру / Не потрібно» от клиентов
    dp.include_router(form.router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

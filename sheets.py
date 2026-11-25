"""
Работа с Google Таблицей.
Этот модуль ничего не знает про Telegram — так логику потом можно
подключить к любому мессенджеру или сайту.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread

import config

# Заголовки столбцов (на украинском — таблицей пользуется папа)
HEADERS = [
    "№", "Дата", "Клієнт", "Телефон", "Telegram ID",
    "Авто", "Рік", "VIN", "Що потрібно", "Статус", "Ціна", "Термін", "Коментар",
]

KYIV = ZoneInfo("Europe/Kyiv")

_worksheet: gspread.Worksheet | None = None


def _get_worksheet() -> gspread.Worksheet:
    """Подключаемся к таблице один раз и запоминаем лист."""
    global _worksheet
    if _worksheet is None:
        client = gspread.service_account(filename=config.GOOGLE_CREDENTIALS)
        # Можно вставить и полную ссылку на таблицу, и только её ID
        if config.SHEET_ID.startswith("http"):
            spreadsheet = client.open_by_url(config.SHEET_ID)
        else:
            spreadsheet = client.open_by_key(config.SHEET_ID)
        _worksheet = spreadsheet.sheet1
        # Если таблица пустая — ставим заголовки
        if _worksheet.row_values(1) != HEADERS:
            _worksheet.update(range_name="A1", values=[HEADERS])
            _worksheet.freeze(rows=1)
    return _worksheet


def _append_row(values: list) -> int:
    """Добавляет строку и возвращает её номер заявки."""
    ws = _get_worksheet()
    number = len(ws.col_values(1))  # заголовок + уже существующие заявки
    now = datetime.now(KYIV).strftime("%d.%m.%Y %H:%M")
    # RAW — чтобы телефон "+380..." не превратился в число или формулу
    ws.append_row([number, now, *values], value_input_option="RAW")
    return number


async def append_test_row() -> int:
    """gspread синхронный, поэтому запускаем его в отдельном потоке, чтобы бот не зависал."""
    row = ["ТЕСТ", "", "", "", "", "", "Перевірка зв'язку", "Нова", "", "", ""]
    return await asyncio.to_thread(_append_row, row)


async def append_request(data: dict) -> int:
    """Записывает заявку клиента. data — ответы из формы."""
    row = [
        data["client"], data["phone"], str(data["tg_id"]),
        data["car"], data["year"], data["vin"], data["part"],
        "Нова", "", "", "",
    ]
    return await asyncio.to_thread(_append_row, row)


# --- Чтение и обновление заявок (этапы 5–6) ---

# Индексы столбцов (с нуля) в строке таблицы
COL_NUMBER, COL_DATE, COL_CLIENT, COL_PHONE, COL_TG_ID = 0, 1, 2, 3, 4
COL_CAR, COL_YEAR, COL_VIN, COL_PART = 5, 6, 7, 8
COL_STATUS, COL_PRICE, COL_TERM, COL_COMMENT = 9, 10, 11, 12


def _row_to_dict(row: list[str]) -> dict:
    row = row + [""] * (len(HEADERS) - len(row))  # пустые ячейки в конце gspread обрезает
    return {
        "number": row[COL_NUMBER], "date": row[COL_DATE], "client": row[COL_CLIENT],
        "phone": row[COL_PHONE], "tg_id": row[COL_TG_ID], "car": row[COL_CAR],
        "year": row[COL_YEAR], "vin": row[COL_VIN], "part": row[COL_PART],
        "status": row[COL_STATUS], "price": row[COL_PRICE], "term": row[COL_TERM],
        "comment": row[COL_COMMENT],
    }


def _find_row(ws: gspread.Worksheet, number: int) -> int | None:
    """Ищем строку по номеру заявки (а не считаем), вдруг папа удалил или переставил строки."""
    for index, value in enumerate(ws.col_values(COL_NUMBER + 1), start=1):
        if index > 1 and value.strip() == str(number):
            return index
    return None


def _get_request(number: int) -> dict | None:
    ws = _get_worksheet()
    row_index = _find_row(ws, number)
    if row_index is None:
        return None
    return _row_to_dict(ws.row_values(row_index))


def _update_answer(number: int, status: str, price: str, term: str) -> bool:
    ws = _get_worksheet()
    row_index = _find_row(ws, number)
    if row_index is None:
        return False
    # Столбцы J–L: Статус, Ціна, Термін
    ws.update(range_name=f"J{row_index}:L{row_index}", values=[[status, price, term]],
              value_input_option="RAW")
    return True


def _update_status(number: int, status: str, comment: str | None) -> bool:
    ws = _get_worksheet()
    row_index = _find_row(ws, number)
    if row_index is None:
        return False
    ws.update(range_name=f"J{row_index}", values=[[status]], value_input_option="RAW")
    if comment is not None:  # столбец M «Коментар»
        ws.update(range_name=f"M{row_index}", values=[[comment]], value_input_option="RAW")
    return True


def _client_requests(tg_id: int, limit: int) -> list[dict]:
    rows = _get_worksheet().get_all_values()[1:]
    mine = [_row_to_dict(r) for r in rows if len(r) > COL_TG_ID and r[COL_TG_ID].strip() == str(tg_id)]
    return mine[-limit:]


async def get_request(number: int) -> dict | None:
    """Заявка по номеру или None, если такой нет."""
    return await asyncio.to_thread(_get_request, number)


async def update_answer(number: int, status: str, price: str = "", term: str = "") -> bool:
    """Записывает ответ магазина. False — заявка не найдена."""
    return await asyncio.to_thread(_update_answer, number, status, price, term)


async def update_status(number: int, status: str, comment: str | None = None) -> bool:
    """Меняет только статус (и, если передан, комментарий). False — заявка не найдена."""
    return await asyncio.to_thread(_update_status, number, status, comment)


async def client_requests(tg_id: int, limit: int = 5) -> list[dict]:
    """Последние заявки клиента (старые сверху, новые снизу)."""
    return await asyncio.to_thread(_client_requests, tg_id, limit)

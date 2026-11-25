# Образ бота АвтоХата. Секреты (.env, credentials.json) в образ НЕ попадают —
# они подключаются при запуске через docker-compose.
FROM python:3.15-slim

# Не создавать .pyc, сразу писать логи (иначе docker logs показывает их с задержкой)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Kyiv

WORKDIR /app

# Сначала только зависимости: при изменении кода этот слой берётся из кэша, сборка быстрее
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Запускаем не от root — так безопаснее
RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot

CMD ["python", "bot.py"]

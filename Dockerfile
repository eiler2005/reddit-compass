# reddit-compass — Python 3.12 + Playwright + Chromium.
# Базовый образ: официальный Playwright (Chromium уже установлен, apt-get не нужен).
# Портативно: весь сервис = этот образ + volume /data.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Устанавливаем пакет reddit-compass (console-script + зависимости).
# .[embed] добавляет лёгкий torch-free model2vec для embedding_v2 (без sentence-transformers/torch).
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir ".[embed]" && playwright install chromium

# Каталог данных (в проде монтируется volume поверх).
RUN mkdir -p /data && useradd -m reddit && chown -R reddit:reddit /data /app
USER reddit

ENV DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["reddit-compass"]
CMD ["all"]

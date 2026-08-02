# reddit-compass — Python 3.12 + Playwright + Chromium.
# Базовый образ: официальный Playwright (Chromium уже установлен, apt-get не нужен).
# Портативно: весь сервис = этот образ + volume /data.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Устанавливаем пакет reddit-compass (console-script + зависимости).
# .[embed] — лёгкий torch-free model2vec для embedding_v2.
# .[adjudicate] добавляет sentence-transformers: он нужен стадии cross-encoder,
# которая разбирает серую зону Stories. Без неё три пола полноты не берутся
# (51.9 multi/1k при поле 65 на 2026-07-26_2026-08-01-broad-r2). Стадия считает
# ~6000 пар за минуту на CPU — дешевле построчного LLM-ревью, которое успевало 80.
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir ".[embed,adjudicate]" && playwright install chromium

# Каталог данных (в проде монтируется volume поверх).
RUN mkdir -p /data && useradd -m reddit && chown -R reddit:reddit /data /app
USER reddit

ENV DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["reddit-compass"]
CMD ["all"]

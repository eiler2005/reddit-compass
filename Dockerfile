# reddit-compass — Python 3.12 + Playwright + Chromium.
# Портативно: весь сервис = этот образ + volume /data.
FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для Chromium.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем пакет reddit-compass (console-script + зависимости).
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir . && \
    python -m playwright install chromium

# Каталог данных (в проде монтируется volume поверх).
RUN mkdir -p /data && useradd -m reddit && chown -R reddit:reddit /data /app
USER reddit

ENV DATA_DIR=/data
VOLUME ["/data"]

# По умолчанию — полный цикл сбора.
CMD ["reddit-compass", "all"]

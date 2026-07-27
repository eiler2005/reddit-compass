# Деплой reddit-compass на HostKey «Hermes»

> Рабочий runbook. Стек развёрнут и работает с 2026-07-22.

## Архитектура: VPS + Mac

```
┌─── VPS HostKey (204.168.239.217) — автоматически, cron ──────────────┐
│  Скидка Qwen 17:00–03:00 МСК = 14:00–00:00 UTC                        │
│                                                                        │
│  14:00 UTC  reddit-compass rss       → 227 статей (BBC, Guardian...)  │
│  14:10 UTC  reddit-compass hn        → 197 stories (HN, 7 дней)       │
│  14:20 UTC  reddit-compass ladder    → 183 статьи (NYT, WaPo, FT...)  │
│  14:30 UTC  reddit-compass ph        → 30 продуктов (ProductHunt)     │
│  15:00 UTC  reddit-compass signals   → LLM-анализ (qwen3.8-max)       │
│  15:30 UTC  reddit-compass radar     → отчёт с ссылками + темами      │
│                                                                        │
│  rc-api     (FastAPI :8900, 24/7)                                      │
│  rc-caddy   (reverse proxy, loopback)                                  │
│  ladder     (paywall proxy, Docker network)                            │
│                                                                        │
│  Dashboard: https://rc.204.168.239.217.sslip.io/dashboard             │
│             (Basic Auth, credentials in .env.secrets, Let's Encrypt)   │
└────────────────────────────────────────────────────────────────────────┘

┌─── Mac (residential IP) — вручную или launchd ─────────────────────────┐
│                                                                        │
│  reddit-compass fetch --stealth   → 737 постов Reddit (18 сабреддитов)│
│  reddit-compass signals           → LLM-анализ (Qwen API)             │
│  scp data/ → VPS                  → sync на сервер                     │
│                                                                        │
│  Причина: Reddit блокирует датацентр-IP (403).                         │
│  Только residential IP (Mac) работает для Reddit.                      │
└────────────────────────────────────────────────────────────────────────┘
```

## Контейнеры

| Контейнер | Образ | Роль | Порт |
|---|---|---|---|
| `rc-api` | reddit-compass-api:latest | FastAPI REST API + Dashboard | 8900 (loopback) |
| `rc-caddy` | caddy:2-alpine | Reverse proxy | 8900→80 (loopback) |
| `ladder` | ghcr.io/everywall/ladder | Paywall proxy (12 СМИ) | 8080 (Docker net) |
| `rc-collector` | reddit-compass-collector:latest | Batch (cron, не daemon) | — |

## Раскладка на VPS

```
/opt/reddit-compass/
├── docker-compose.yml      # 3 сервиса (api + caddy + collector)
├── Dockerfile              # Playwright (batch collector)
├── Dockerfile.api          # Slim (API, без Chromium)
├── Caddyfile               # Reverse proxy :80 → api:8900
├── .env                    # Секреты (LADDER_URL, PRODUCTHUNT_API_KEY, RC_API_*)
├── src/                    # Исходники (sync с Mac)
├── config/                 # Профили
└── data/                   # Volume: snapshots/, compass.db
```

## Host-cron (скидка Qwen 17:00–03:00 МСК = 14:00–00:00 UTC)

LLM-анализ (signals) запускается в окне скидки на qwen3.8-max-preview.
Весь nightly заканчивается до 03:00 МСК (00:00 UTC).

```cron
# Сбор данных (без LLM, бесплатно)
0  14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass rss >> /tmp/rc-rss.log 2>&1
10 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass hn >> /tmp/rc-hn.log 2>&1
20 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass ladder >> /tmp/rc-ladder.log 2>&1
30 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass ph >> /tmp/rc-ph.log 2>&1
# LLM-анализ (в окне скидки 17:00-03:00 МСК)
0  15 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass signals >> /tmp/rc-signals.log 2>&1
30 15 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass radar >> /tmp/rc-radar.log 2>&1
```

> Reddit fetch — вручную с Mac (residential IP), sync через чат. С VPS Reddit блокирует (403).

## Модельная пирамида (цена/качество)

| Задача | Модель | Почему |
|---|---|---|
| Синтез (темы, идеи колонок, сдвиги) | `qwen3.8-max-preview` | Сложное, мало вызовов, скидка 17:00–03:00 МСК |
| Классификация постов (pain points, relevance) | `qwen3.7-plus` | Массово, баланс цена/качество |
| Простые задачи (фильтрация, саммари) | `qwen3.6-flash` | Самый дешёвый |

## HTTPS-доступ (Caddy на хосте)

```
/etc/caddy/Caddyfile:
  https://rc.204.168.239.217.sslip.io {
      basicauth { admin <bcrypt-hash> }
      reverse_proxy 127.0.0.1:8900
  }
```

- DNS: sslip.io (автоматически, без настройки)
- TLS: Let's Encrypt (авто)
- Auth: Basic Auth (credentials in `.env.secrets`)

## Ladder (paywall proxy)

```bash
# Запущен как отдельный контейнер, подключён к reddit-compass_net:
docker run -d --restart always --name ladder -p 127.0.0.1:8080:8080 \
  --env RULESET=https://raw.githubusercontent.com/everywall/ladder-rules/main/ruleset.yaml \
  ghcr.io/everywall/ladder:latest
docker network connect reddit-compass_net ladder

# Из контейнера reddit-compass доступен как http://ladder:8080
# .env: LADDER_URL=http://ladder:8080
```

## Жёсткие границы

1. Только `vps-hostkey-hermes`. Hetzner и другие стеки не трогать.
2. Изолированный стек `/opt/reddit-compass` (своя сеть + volume).
3. Reddit fetch — **только с Mac** (residential IP). С VPS — 403.
4. UFW: порт 8900 открыт. Остальное — default deny.
5. Секреты в `.env` (gitignored). Никогда в git.

## Проверки

```bash
# Health (credentials из .env.secrets)
curl -u "$RC_BASIC_AUTH" https://rc.204.168.239.217.sslip.io/health

# Dashboard
open https://rc.204.168.239.217.sslip.io/dashboard

# Логи
ssh deploy@204.168.239.217 "tail -5 /var/log/reddit-compass/rss.log"

# Данные
ssh deploy@204.168.239.217 "docker exec rc-api ls /data/snapshots/"
```

## Резервные копии

Volume `reddit-compass_data` — по регламенту `vps_management`.
Секретов в данных нет (только JSONL + SQLite + Markdown).

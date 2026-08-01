# Деплой reddit-compass на HostKey «Hermes»

> Рабочий runbook. Стек развёрнут и работает с 2026-07-22.

## Архитектура: VPS + Mac

```
┌─── VPS HostKey (${RC_DEPLOY_HOST}) — автоматически, cron ──────────────┐
│  Скидка Qwen 17:00–03:00 МСК = 14:00–00:00 UTC                        │
│                                                                        │
│  14:00 UTC  rss / hn / ladder / ph snapshots                          │
│  14:45 UTC  collect --from-snapshots → один raw run                   │
│  16:00 UTC  Engine → stories → Qwen → trends → quality → shadow       │
│  вручную    complete gated publication → broad                          │
│                                                                        │
│  rc-api     (FastAPI :8900, 24/7)                                      │
│  rc-caddy   (reverse proxy, loopback)                                  │
│  ladder     (paywall proxy, Docker network)                            │
│                                                                        │
│  Dashboard: ${RC_PUBLIC_BASE_URL}/today                                │
│             (Basic Auth, credentials in .env.secrets, TLS by proxy)     │
└────────────────────────────────────────────────────────────────────────┘

┌─── Mac (residential IP) — вручную или launchd ─────────────────────────┐
│                                                                        │
│  reddit-compass fetch --stealth   → Reddit snapshot из broad packs     │
│  reddit-compass engine cycle      → versioned Story/Trend analysis     │
│  scp data/ → VPS                  → sync на сервер                     │
│                                                                        │
│  Reddit собирается отдельно, если серверный маршрут недоступен.         │
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

Collector и Engine запускаются раздельно. LLM не влияет на статус raw collection;
он оценивает лишь ограниченную серую зону Story/Trend Engine. Broad не обновляется от
partial Data Release или failed quality gate.

```cron
# Snapshots (source adapters)
0  14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass rss >> /tmp/rc-rss.log 2>&1
10 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass hn >> /tmp/rc-hn.log 2>&1
20 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass ladder >> /tmp/rc-ladder.log 2>&1
30 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass ph >> /tmp/rc-ph.log 2>&1
45 14 * * * cd /opt/reddit-compass && docker compose run --rm reddit-compass collect --from-snapshots --profile broad --sources reddit,hn,rss,ladder,ph --date $(date -u +\%F) >> /tmp/rc-finalize.log 2>&1
0 16 * * * cd /opt/reddit-compass && docker compose run --rm -e HF_HOME=/data/.cache/hf reddit-compass engine cycle --profile broad --window 7 --review-limit 80 --trend-review-limit 12 --publish-channel shadow >> /tmp/rc-engine.log 2>&1
```

Установка block идемпотентна и сохраняет cron других стеков:

```bash
INSTALL_CRON=1 ./deploy/hostkey/deploy.sh
```

Reddit поступает с local approved route как `posts.jsonl` в Docker volume. Локальный
`compass.db` никогда не копируется на VPS. Полный completion/publish contract:
[`docs/COLLECTION_LIFECYCLE.md`](../../docs/COLLECTION_LIFECYCLE.md).

## Модельная пирамида (цена/качество)

| Задача | Модель | Почему |
|---|---|---|
| Синтез (темы, идеи колонок, сдвиги) | `qwen3.8-max-preview` | Сложное, мало вызовов, скидка 17:00–03:00 МСК |
| Классификация постов (pain points, relevance) | `qwen3.7-plus` | Массово, баланс цена/качество |
| Простые задачи (фильтрация, саммари) | `qwen3.6-flash` | Самый дешёвый |

## HTTPS-доступ (Caddy на хосте)

Реализовано в [`Caddyfile`](Caddyfile) этого каталога — отдельный host-Caddy
не нужен, всё делает `rc-caddy` из compose.

- Имя: `RC_PUBLIC_HOST`, сейчас sslip.io — резолвится в IP без своей DNS-зоны
- TLS: Let's Encrypt по HTTP-01 (порт 80 открыт только ради challenge)
- Порт: `RC_PUBLIC_HTTPS_PORT` (не 443 — он занят L4-роутером соседнего проекта)
- Auth: Basic Auth, хэш в `RC_BASIC_HASH` в `.env.secrets`

Подробнее, включая смену пароля и то, почему Docker публикует порты мимо UFW:
[`docs/HOSTING.md`](../../docs/HOSTING.md).

## Ladder (paywall proxy)

Ladder — сервис в [`docker-compose.yml`](docker-compose.yml), поднимается
вместе со стеком. Коллектор ходит к нему по `http://ladder:8080` внутри сети.

Раньше он запускался руками и не входил ни в один compose-проект — из-за этого
при переезде он держал сеть и не поехал бы следом за сервисом:

```bash
# Как было (больше не используется):
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
3. Reddit fetch — read-only public data; при проблемах серверного маршрута используется локальный residential route.
4. UFW: порт 8900 открыт. Остальное — default deny.
5. Секреты в `.env` (gitignored). Никогда в git.

## Проверки

```bash
# Health (credentials из .env.secrets)
curl -u "$RC_BASIC_AUTH" "${RC_PUBLIC_BASE_URL}/health"

# Dashboard
open "${RC_PUBLIC_BASE_URL}/today"

# Логи
ssh "${RC_DEPLOY_USER:-deploy}@${RC_DEPLOY_HOST:-reddit-compass-vps}" "tail -5 /var/log/reddit-compass/rss.log"

# Данные
ssh "${RC_DEPLOY_USER:-deploy}@${RC_DEPLOY_HOST:-reddit-compass-vps}" "docker exec rc-api ls /data/snapshots/"
```

## Резервные копии

Volume `reddit-compass_data` — по регламенту `vps_management`.
Секретов в данных нет (только JSONL + SQLite + Markdown).

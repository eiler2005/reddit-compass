# Reddit Access Matrix

Тестировано 2026-07-31 — 2026-08-01. Без секретов и IP-адресов.

## Не работает (никогда не использовать)

| Схема | Хост | Движок | Proxy | Результат | Почему |
|-------|------|--------|-------|-----------|--------|
| 1 | VPS | aiohttp / curl | — | 403 | DC IP заблокирован для bare HTTP |
| 2 | VPS | aiohttp / curl | residential | 403 | Reddit режет proxy pool без browser context |
| 3 | Mac | aiohttp | residential | 403 | То же: proxy pool IP помечены Reddit |

**Общее правило:** bare HTTP (aiohttp, curl, requests) не работает ни с одного хоста,
ни с proxy, ни без. Reddit требует browser context (cookies + JS fingerprint).

## Работает

| Схема | Хост | Движок | Proxy | Exit IP | Устойчивость | Риск |
|-------|------|--------|-------|---------|--------------|------|
| A | Mac | Playwright | — | домашний residential | Высокая | Домашний IP светится перед Reddit |
| B | Mac | Playwright | residential | proxy exit | Высокая | Зависимость от proxy-сервиса |
| C | VPS | Playwright | — | DC IP | Средняя | DC подсеть могут заблокировать |
| **D** | **VPS** | **Playwright** | **residential** | **proxy exit** | **Высокая** | **Основная схема** |

## Почему схема D — основная

1. **Домашний IP не участвует** — нулевая корреляция с личным аккаунтом Reddit.
2. **DC IP скрыт** за residential exit — Reddit видит обычного домашнего пользователя.
3. **VPS автономен** — собирает Reddit + HN + RSS + Ladder + PH, финализирует,
   запускает Engine, публикует. Mac не нужен для pipeline.
4. **Playwright + cookies** обходят блок, который режет bare HTTP даже через proxy.

## Как работает Playwright-сбор

```
1. chromium.launch(headless=True, proxy=proxy_config)
2. page.goto("https://www.reddit.com/")     ← анонимные session cookies
3. asyncio.sleep(2)                          ← Reddit ставит cookies
4. page.evaluate(fetch("/r/.../hot.json"))   ← запрос из browser context
5. Результат: валидный JSON с постами
```

Ключевой момент: `fetch()` выполняется **внутри** browser context, с cookies
и browser fingerprint. Reddit видит это как обычный браузерный запрос.

## Конфигурация схемы D

В `deploy/hostkey/docker-compose.yml` для collector-сервиса:

```yaml
environment:
  REDDIT_COMPASS_ENGINE: playwright
```

Proxy передаётся через `env_file: [.env]` (переменная `REDDIT_COMPASS_PROXIES`
в `.env.secrets`, синхронизирована на VPS как `.env`).

VPS cron собирает Reddit через `collect --sources reddit --stealth`
до финализации `--from-snapshots`.

## Mac как резерв

`scripts/fetch-and-sync.sh` остаётся как fallback:
- Если VPS не смог собрать Reddit (proxy упал, rate limit)
- Mac собирает через Playwright (схема A или B)
- scp posts.jsonl на VPS → trigger финализации

## Риски и митигации

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Бан личного Reddit-аккаунта | ~0% | Нет связи аккаунт ↔ коллектор (разные cookies, разные сессии) |
| Блок домашнего IP | Низкая | Схема D не использует домашний IP |
| Блок DC IP | Средняя | Proxy скрывает DC IP за residential exit |
| Блок proxy exit IP | Низкая | Sticky TTL + ротация; один блок ≠ потеря канала |
| Бан proxy-аккаунта | Средняя | Умеренный темп (1 запуск/день), read-only трафик |
| Reddit меняет anti-bot | Средняя | Playwright + cookies — последний fallback; если и он упадёт, нужен OAuth |

## История тестирования

| Дата | Хост | Что тестировали | Результат |
|------|------|-----------------|-----------|
| 2026-07-24 | Mac | aiohttp + IPRoyal | 403 |
| 2026-07-26 | Mac | aiohttp + IPRoyal (Skip ISP Static) | 403 |
| 2026-07-31 | VPS | curl direct | 403 |
| 2026-07-31 | VPS | curl + IPRoyal | 403 |
| 2026-07-31 | VPS | Playwright direct (goto + fetch) | 200 |
| 2026-07-31 | VPS | Playwright + IPRoyal (goto + fetch) | 200 |
| 2026-08-01 | Mac | Playwright direct (fetch-and-sync) | 200 |

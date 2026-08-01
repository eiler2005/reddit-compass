# Reddit Access Architecture

Как reddit-compass получает данные из Reddit и почему выбран текущий маршрут.

## Принцип

Сервис read-only: только публичные посты и комментарии, без аккаунта,
без постинга, без голосования. Данные собираются через публичные endpoints
Reddit с соблюдением rate limit (4с между запросами, retry на 429).

## Текущая схема

VPS собирает Reddit автономно через browser-based engine с residential proxy.
Домашний IP не участвует в сборе. Mac-скрипт `fetch-and-sync.sh` — резерв.

Почему browser-based engine: Reddit требует полноценный browser context
для доступа к данным. Bare HTTP-запросы (без JS execution и cookies)
не проходят проверку даже с residential IP.

Почему residential proxy: скрывает IP дата-центра за обычным домашним
адресом. Reddit видит трафик как от обычного пользователя.

## Резервный канал

Если VPS не может собрать Reddit (proxy недоступен, rate limit),
Mac собирает данные локально и синхронизирует артефакт на VPS через
`scripts/fetch-and-sync.sh`. Скрипт также триггерит финализацию
и Engine cycle на VPS после передачи данных.

## Ограничения

- Proxy-сервис — внешняя зависимость; при его недоступности сбор
  переключается на Mac или откладывается до следующего цикла.
- Rate limit Reddit (429) обрабатывается автоматически с retry.
- При изменении anti-bot политики Reddit может потребоваться
  обновление browser engine или переход на OAuth API.

## См. также

- `docs/PROXY_OPERATIONS.local.md` — операционные детали proxy (gitignored)
- `docs/REDDIT_ACCESS_MATRIX.local.md` — полная матрица схем и рисков (gitignored)
- `AGENTS.md` — границы Reddit (read-only, rate limit, proxy policy)

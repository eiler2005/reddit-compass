# Хостинг: где живёт сервис и как его переносить

## Где сейчас

Сервис работает на VPS **`vps-hostkey-hermes`** (алиас из инвентаря
[`vps_management`](https://github.com/eiler2005/vps_management)). Адрес и учётные
данные лежат в vault того проекта — в этом репозитории их нет.

| | |
|---|---|
| Каталог | `/opt/reddit-compass` |
| Контейнеры | `rc-api`, `rc-caddy`, `ladder`, разовый `rc-collector` |
| Данные | docker volume `reddit-compass_data` (~2.2GB) |
| Порты | `127.0.0.1:8900` (Caddy → API), `127.0.0.1:8943`, API на `127.0.0.1:8901` |
| Публичного домена нет | наружу не смотрит ничего |

## Как открыть интерфейс

Два входа в один и тот же API.

### Публичный: HTTPS + Basic Auth

```
https://${RC_PUBLIC_HOST}:${RC_PUBLIC_HTTPS_PORT}/today
```

Логин и пароль — `RC_BASIC_USER` / хэш в `RC_BASIC_HASH` (`deploy/hostkey/.env.secrets`).

**Авторизации в самом интерфейсе нет**, поэтому Basic Auth здесь — единственное,
что стоит между интернетом и всем собранным корпусом. Без ключей
`RC_PUBLIC_HOST` / `RC_BASIC_*` публичный сайт не поднимается вовсе: остаётся
только loopback.

Имя — любая A-запись на IP хоста. Сейчас используется **sslip.io**
(`compass.<IP-через-дефисы>.sslip.io`): он резолвится в IP без своей DNS-зоны,
и Let's Encrypt выдаёт на него обычный сертификат.

Сменить пароль:

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext '<новый>'
# результат → RC_BASIC_HASH в .env.secrets, обязательно в одинарных кавычках
./deploy/hostkey/deploy.sh
```

Кавычки обязательны: bcrypt начинается с `$2a$`, а `deploy.sh` подключает файл
через `. .env.secrets` при `set -u` и без них падает с `$2: unbound variable`.

### Локальный: SSH-туннель, без пароля

```bash
ssh -N -L 8900:127.0.0.1:8900 <user>@<host>
# в браузере: http://localhost:8900/today
```

Хост — из `RC_DEPLOY_HOST` либо через `vps_management`:

```bash
cd ../vps_management && ./ansible/scripts/ssh-vps.sh --host vps-hostkey-hermes --print-host
```

С самого хоста — просто `curl http://127.0.0.1:8900/today`.

### Какие порты открыты и почему

| Порт | Куда смотрит | Зачем |
|---|---|---|
| `127.0.0.1:8900` | контейнерный `:80` | локальный вход без пароля |
| `0.0.0.0:80` | контейнерный `:80` | **только** ACME HTTP-01, сайт на нём не отдаётся |
| `0.0.0.0:8450` | HTTPS | публичный вход, закрыт Basic Auth |
| `127.0.0.1:8901` | `rc-api` напрямую | диагностика в обход Caddy |

Порт **443 не наш**: его держит L4-роутер соседнего проекта (SNI-маршрутизация
на xray и на inner-caddy приложения `cheap-intelligence`), и его маршрут
привязан к одному имени с проверкой в плейбуке `router_configuration`.
Поэтому публичный HTTPS живёт на своём порту, а чужой конфиг не трогается.

Чтобы получить адрес без порта, нужно научить тот роутер второму SNI —
это изменение в `router_configuration`, не здесь.

### Docker публикует порты мимо UFW

Если в `ports:` не указан явно `127.0.0.1`, Docker пишет DNAT
`0.0.0.0/0 → dpt:<порт>` прямо в цепочку `DOCKER` таблицы `nat`.
`default deny (incoming)` в UFW на это не влияет: пакет не доходит до его
цепочек.

Именно так и было до 2026-08-01: порт `8900` отвечал из интернета **без
авторизации**, хотя `Caddyfile` и `ARCHITECTURE.md` заявляли «loopback only,
без публичных портов». Обнаружилось при попытке ответить на вопрос «какая
теперь ссылка».

Проверка после любого изменения портов:

```bash
# без пароля — обязан быть 401
curl -s -o /dev/null -w '%{http_code}\n' https://${RC_PUBLIC_HOST}:8450/today

# незащищённый вход снаружи — обязан быть недоступен
curl --max-time 5 http://<host>:8900/health

# правила: 8900 и 8901 обязаны вести на 127.0.0.1
sudo iptables -t nat -L DOCKER -n | grep -E '8900|8901'
```

Это же закреплено тестом `test_only_authenticated_entry_points_face_the_internet`.

Целевой хост берётся из `RC_DEPLOY_HOST` в `deploy/hostkey/.env.secrets` —
это **единственный** источник правды. Его читают и `deploy.sh`, и
`scripts/fetch-and-sync.sh` (ночная заливка Reddit-снимков с Mac).

## Переезд 2026-08-01: Hetzner → HostKey Hermes

### Почему

Прежний хост (`vps-hetzner-prod`) упёрся в диск: 38GB, занято 92%, а
физический диск — 41.0GB, из которых раздел уже занимал 40.7GB. Свободных
секторов не было, то есть расширить изнутри было нечего — только апгрейд тарифа.
Деплой падал с `no space left on device` уже после успешной компиляции.

При этом сервис делил хост с восемью чужими проектами, и его доля составляла
примерно четверть диска. Соседняя машина простаивала:

| | vps-hetzner-prod | vps-hostkey-hermes |
|---|---|---|
| Диск | 38G, свободно 3.2G | 157G, свободно 138G |
| RAM | 3.7Gi | 15Gi |
| CPU | 2 | 8 |

Ночной цикл движка идёт 30–60 минут и упирается в память на эмбеддингах, так что
переезд решал не только вопрос места.

### Что оказалось неочевидным

**Ladder был запущен руками.** Контейнер `ladder` — источник кластера
ladder-paywall — не входил ни в один compose-проект: его подняли через
`docker run` и подключили к сети `reddit-compass_net`. Обнаружилось это дважды:
сеть отказалась удаляться («resource is still in use»), и источник не поехал бы
следом за сервисом. Теперь ladder — часть `docker-compose.yml` и переезжает вместе
со стеком.

**`LADDER_URL` указывал на `localhost:8080`.** Внутри контейнера это его
собственный loopback, а не прокси; на новом хосте порт 8080 к тому же занят чужим
сервисом. Значение заменено на `http://ladder:8080` — имя сервиса в сети compose
не зависит ни от хоста, ни от занятых портов.

### Как это делалось

```bash
# 1. Код и образы на новый хост
RC_DEPLOY_HOST=<новый> ./deploy/hostkey/deploy.sh

# 2. Остановить запись на обоих хостах — снимок SQLite должен быть целостным
ssh <старый> "cd /opt/reddit-compass && docker compose stop api"
ssh <новый>  "cd /opt/reddit-compass && docker compose stop api"

# 3. Перелить volume одной трубой: промежуточный файл на 2GB некуда положить,
#    на хосте-источнике свободно всего 3G
ssh <старый> "docker run --rm -v reddit-compass_data:/d alpine tar -czf - -C /d ." \
| ssh <новый> "docker run --rm -i -v reddit-compass_data:/d alpine sh -c \
    'rm -rf /d/* /d/.[!.]*; tar -xzf - -C /d && chown -R 1002:1002 /d'"

# 4. Сверить контрольные суммы на обоих хостах
docker run --rm -v reddit-compass_data:/d alpine md5sum /d/*.db

# 5. Запустить и проверить
ssh <новый> "cd /opt/reddit-compass && docker compose up -d api"
ssh <новый> "curl -s http://127.0.0.1:8900/version"

# 6. Перенести ночную автоматизацию
ssh <старый> "crontab -l | sed '/# BEGIN reddit-compass managed pipeline/,/# END reddit-compass managed pipeline/d' | crontab -"
ssh <новый>  "/opt/reddit-compass/install-cron.sh"

# 7. Переключить источник правды
#    RC_DEPLOY_HOST в deploy/hostkey/.env.secrets → новый хост
```

**Права на volume.** Контейнер API работает от `1002:1002`; после распаковки
владельца нужно выставить явно, иначе сервис не сможет писать.

### Проверка после переезда

```bash
ssh <новый> "curl -s http://127.0.0.1:8900/version"   # git_sha == git rev-parse --short HEAD
ssh <новый> "cd /opt/reddit-compass && docker compose run --rm reddit-compass hn"
```

Второй вызов — дымовой тест конвейера: он ходит в сеть, пишет снимок в volume
и подтверждает, что сбор жив на новом хосте.

Ladder проверяется отдельно, потому что именно он ломается при смене хоста:

```bash
ssh <новый> "cd /opt/reddit-compass && docker compose run --rm --entrypoint python reddit-compass -c \
  \"import os, urllib.request; print(urllib.request.urlopen(os.environ['LADDER_URL']).status)\""
```

### Откат

Пока старый хост не вычищен, откат — это вернуть `RC_DEPLOY_HOST` на прежнее
значение и поднять там стек. После вычистки откатываться некуда: volume удалён,
и единственная копия данных живёт на новом хосте.

**Поэтому вычистка делается только после того, как на новом хосте сверены
контрольные суммы и прошёл дымовой тест.**

### Что осталось на старом хосте

Ничего: контейнеры, образы, volumes, `/opt/reddit-compass` и cron удалены,
кэш сборки очищен. Диск освободился с 91% до 71% (7.5GB). Чужие сервисы
не затрагивались.

## Гигиена диска

Каждая пересборка оставляет предыдущий образ висячим и добавляет несколько
гигабайт кэша. На 38-гигабайтном диске двух деплоёв подряд хватало, чтобы
упереться в 100%.

- `deploy.sh` чистит **до** сборки: место нужно именно в этот момент.
- Ночной cron (`03:30`) повторяет чистку.

Чистится только заведомо ненужное — висячие образы и кэш сверх 2GB:

```bash
docker image prune -f
docker builder prune -f --keep-storage 2GB
```

**Ни `docker system prune`, ни `-a`, ни volumes.** На этих хостах работают чужие
сервисы: `system prune` снёс бы их остановленные контейнеры, `-a` — образы,
которые сейчас не запущены, но нужны.

## Связанные документы

- [`VERSIONING.md`](VERSIONING.md) — как понять, что развёрнуто и на каких данных
- [`COLLECTION_LIFECYCLE.md`](COLLECTION_LIFECYCLE.md) — контракт сбора и handoff Mac→VPS
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — границы и потоки

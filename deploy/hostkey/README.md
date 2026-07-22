# Деплой reddit-compass на HostKey «Hermes» (Roadmap Phase 2)

> Это runbook с обязательными границами, а не набор команд для слепого копирования. Перед любым
> изменением сверить живое состояние HostKey и актуальные правила в `vps_management` и
> `router_configuration`. Скелет создан заранее; включать деплой — только по явному подтверждению.

## Жёсткие границы

1. Разворачивать **только на `vps-hostkey-hermes`**. `vps-hetzner-prod` и любые сервисы на Hetzner
   не трогать.
2. Отдельный app-owned стек `/opt/reddit-compass`, изолированный от `/opt/stealth`,
   `/opt/moex-futoi`, `/opt/cheap-intelligence` (своя сеть + volume). Не добавлять сервис в чужие
   compose-файлы.
3. **Никаких публичных портов.** reddit-compass — batch-job: пишет в volume, наружу ничего не
   отдаёт. Публичный `:443`/Caddy SNI появится только с веб-дашбордом (Roadmap Phase 5).
4. Регистрация владельца, контейнера, volume, backup и расписания — в `vps_management`.

## Раскладка

```
/opt/reddit-compass/
  docker-compose.yml     # из deploy/hostkey/ (этот стек)
  .env                   # секреты и переопределения, НЕ в git
  # образ собирается из исходников reddit-compass (build: ../..) или тянется из registry
```

## Ночной прогон (host-cron)

Расписание на хосте (не внутри контейнера). Пример строки crontab (UTC), ежедневно в 03:17:

```cron
17 3 * * * cd /opt/reddit-compass && /usr/bin/docker compose run --rm reddit-compass nightly >> /var/log/reddit-compass/nightly.log 2>&1
```

Данные копятся в volume `reddit-compass_data` (`/data` внутри контейнера): `snapshots/<date>/` и
`harvests/reddit-compass-<date>.md`.

## Проверки

```bash
docker compose config                     # валидация стека
docker compose run --rm reddit-compass all
docker run --rm -v reddit-compass_data:/d alpine ls -R /d/snapshots | head
```

## Резервные копии

Бэкапить volume `reddit-compass_data` по общему регламенту `vps_management` (снапшоты JSONL и
Markdown — единственное состояние сервиса; секретов в данных нет).

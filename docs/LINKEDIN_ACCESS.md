# LinkedIn Access Architecture

Как reddit-compass получает данные из LinkedIn и почему выбран текущий маршрут.
LinkedIn — **отдельный канал сбора** наряду с Reddit: у него своя модель доступа,
свои лимиты и свои точки отказа. Общее с Reddit — только контракт данных
(`PostCard` → JSONL → `ContentItem`) и этические границы проекта.

## Принцип

Сервис read-only: только публичные посты и статьи, без аккаунта, без логина,
без постинга. Данные читаются гостем — так же, как их видит любой посетитель
без сессии. Rate limit: пауза ≥4с между загрузками страниц, пауза ≥12с между
поисковыми запросами, на HTTP 429 — backoff (10с → 20с) без наращивания давления.
Прокси в канале **не используются**: проблема LinkedIn не в репутации IP,
а в требовании логина на большинстве поверхностей — прокси её не решают,
а их применение для обхода блоков запрещено политикой проекта.

## Модель доступа (проверена спайком 2026-08-10, VPS + Mac)

| Поверхность | Гостем | Использование |
|---|---|---|
| Профиль `/in/<slug>/` | ❌ 999 → authwall | не используется |
| Лента `/in/<slug>/recent-activity/` | ❌ authwall | не используется |
| Пост `/posts/<slug>_…-activity-<id>-<suffix>` | ✅ 200, голый HTTP без JS | основной объект сбора |
| Статья `/pulse/<slug>` | ✅ 200 | собирается, только если JSON-LD-автор совпадает |
| Поиск постов автора | ✅ через внешний поисковик | discovery |

Страница поста отдаёт гостю JSON-LD постового типа — `SocialMediaPosting`
для текста, `VideoObject`/`ImageObject` для медиа (автор там в `creator`,
текст в `description`); адаптер принимает все варианты:

- `headline` — первая строка поста;
- `articleBody` — превью (~260 символов); полный текст закрыт самим LinkedIn
  (`hasPart.isAccessibleForFree = False`), поэтому content scope — `excerpt`;
- `datePublished` — точный ISO-timestamp;
- `interactionStatistic` — число реакций (LikeAction);
- `commentCount` и первые комментарии (`comment`).

Свежесть кандидата видна **без загрузки страницы**: activity-id — snowflake,
`id >> 22` = миллисекунды UTC (проверено по `datePublished`; расхождение —
минуты, достаточно для фильтра окна).

## Discovery

Профили закрыты, поэтому список постов автора собирается внешним поиском:
Brave Search HTML, запрос `site:linkedin.com/posts <slug>`; запрос по имени —
только если слаговый ничего не дал (квота поисковика ограничена).
DDG/Bing/Mojeek/Ecosia с VPS отдавали 202/403/пусто — не используются.

Ограничения discovery:

- Поисковый индекс запаздывает: самые свежие посты (часы-дни) могут ещё не
  индексироваться. Канал даёт «последние недели», а не real-time.
- Квота Brave на IP конечна: VPS-адрес выдувается за несколько запросов (429),
  у Mac-адреса квота свежая. Адаптер отвечает на 429 backoff'ом и деградацией,
  а не сменой IP.
- Поиск выдаёт и чужие материалы об авторе: посты фильтруются по vanity-слагу
  в URL, статьи `/pulse/` — по JSON-LD `author`.

Надёжный минимум на случай деградации поиска — seeds: прямые URL постов
по слагу (константа `SEED_POST_URLS` в адаптере, параметр `seed_post_urls`
в `fetch_linkedin_authors`; явный пустой dict отключает). Seeds идут без
поисковика, но через тот же фильтр свежести: устаревшие просто выпадут
из окна. Окно по умолчанию — 180 дней: поисковый индекс запаздывает
на недели-месяцы, и узкое окно оставило бы канал пустым.

## Поток данных

```
Brave Search ──URL постов──► фильтр свежести (snowflake) ──► голый aiohttp GET
                                                                    │
                                                              JSON-LD разбор
                                                                    │
                                                                    ▼
                     PostCard (subreddit="linkedin", keyword=<slug>) ──► linkedin.jsonl
                                                                    └──► linkedin-report.md (дайджест)
                                                                    │
                       collect --from-snapshots --sources linkedin  ▼
                                            compass.db items (provider="linkedin",
                                            cluster="voices", scope="excerpt",
                                            source_section=<slug>)
```

Маппинг `PostCard`: `post_id` — activity-id; `author` — из JSON-LD;
`score` — реакции; `num_comments` — commentCount; `top_comments` — первые
комментарии поста; `monitoring_type="linkedin"`. В `ContentItem`
(`intelligence/compat.py`): engagement `{reactions, comments}`,
`source_section` = vanity-слаг автора — даёт разрезку по авторам.

## Команды

```bash
reddit-compass linkedin --dry-run      # план сбора без сети
reddit-compass linkedin                # сбор → data/snapshots/<date>/linkedin.jsonl
                                       #        + linkedin-report.md
reddit-compass collect --from-snapshots --sources linkedin --date YYYY-MM-DD
```

Источник opt-in: в реестре `enabled_by_default=False`, в `DEFAULT_SOURCES`
не входит и ночную сборку не гейтует (`expected_min_items=0`).

## Ограничения

- Только excerpt: полный текст постов гостю не отдаётся — архитектурный факт
  LinkedIn, не баг адаптера.
- Статьи авторов пока находятся поиском нестабильно; чужие статьи об авторе
  отсекаются по JSON-LD-авторству.
- При изменении anti-bot политики LinkedIn канал деградирует честно:
  `RequestTally` поднимает `SourceTransportError` при полном отказе и пишет
  частичные отказы в source health, вместо того чтобы маскировать их пустым «ок».

## Тесты

`tests/test_linkedin.py` — изолированно от Reddit-тестов (другой канал, другая
модель доступа): разбор JSON-LD поста и статьи, фильтрация чужих постов по слагу,
snowflake-время, фильтр свежести, маппинг в `ContentItem`, файловые мэппинги
collector/compat/rebuild/repair, дайджест-отчёт. Только синтетические фикстуры,
без сети — по правилам проекта. Реестровая запись закреплена в
`tests/test_source_registry.py::test_linkedin`.

## Площадки

- **Mac** — основная площадка для discovery: свежая квота Brave, residential IP.
- **VPS** — запуск в batch-образе коллектора (Playwright там не нужен — канал
  работает голым HTTP): `docker compose run --rm reddit-compass linkedin`.
  Квота Brave на VPS-IP выдувается быстро; использовать как резерв площадки.

## См. также

- `docs/REDDIT_ACCESS.md` — Reddit-канал (другая модель: browser engine + proxy)
- `AGENTS.md` — общие границы (read-only, rate limit, proxy policy)
- `ARCHITECTURE.md` §2–§3 — место источника в кластерах и адаптерах

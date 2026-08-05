#!/usr/bin/env bash
# fetch-and-sync.sh — Reddit fetch локально (Mac) + sync на VPS.
#
# Маршрут Reddit-запросов:
#   auto (по умолчанию) — чередование direct approved route / configured proxy;
#   RC_PROXY_MODE=on|off — принудительный выбор маршрута.
# Proxy берётся из REDDIT_COMPASS_PROXIES (deploy/hostkey/.env.secrets).
#
# Запуск:
#   ./scripts/fetch-and-sync.sh          # полный цикл
#   ./scripts/fetch-and-sync.sh --fetch  # только fetch (без sync)
#   ./scripts/fetch-and-sync.sh --sync   # только sync (без fetch)
#
# Ночной автозапуск: scripts/com.reddit-compass.nightly.plist (launchd)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="/opt/reddit-compass"
# DATA_DIR из окружения позволяет прогнать скрипт в изолированный каталог (тесты)
DATA_DIR="${DATA_DIR:-${PROJECT_DIR}/data}"
TODAY=$(date +%Y-%m-%d)

cd "${PROJECT_DIR}"

# ── Секреты и выбор маршрута Reddit ────────────────────────────────────────

SECRETS_FILE="${PROJECT_DIR}/deploy/hostkey/.env.secrets"
if [[ -f "${SECRETS_FILE}" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "${SECRETS_FILE}"
    set +a
fi

VPS_HOST="${RC_DEPLOY_HOST:-reddit-compass-vps}"
VPS_USER="${RC_DEPLOY_USER:-deploy}"

# RC_PROXY_MODE: auto (чередование по дню) | on | off. Без proxy в секретах — off.
resolve_proxy_mode() {
    case "${RC_PROXY_MODE:-auto}" in
        on|off)
            echo "${RC_PROXY_MODE}"
            ;;
        *)
            if [[ -z "${REDDIT_COMPASS_PROXIES:-}" ]]; then
                echo "off"
            elif (( 10#$(date +%d) % 2 == 1 )); then
                echo "on"
            else
                echo "off"
            fi
            ;;
    esac
}

# ── Fetch (локально, домашний IP или IPRoyal proxy) ────────────────────────

do_fetch() {
    local proxy_mode
    proxy_mode="$(resolve_proxy_mode)"
    if [[ "${proxy_mode}" == "on" ]]; then
        # Reddit отдаёт .json браузерному трафику, но режет голый HTTP с pool-IP
        export REDDIT_COMPASS_ENGINE=playwright
        echo "🌐 [$(date +%H:%M:%S)] Маршрут Reddit: configured proxy (движок playwright)"
    else
        unset REDDIT_COMPASS_ENGINE
        echo "🏠 [$(date +%H:%M:%S)] Маршрут Reddit: direct approved route"
    fi

    echo "🔍 [$(date +%H:%M:%S)] Reddit fetch: ${TODAY}"
    echo "============================================================"
    uv run reddit-compass fetch --stealth
    echo ""
    echo "✅ [$(date +%H:%M:%S)] Reddit snapshot готов: ${DATA_DIR}/snapshots/${TODAY}/posts.jsonl"
}

# ── Sync на VPS ────────────────────────────────────────────────────────────

# Идентификатор последнего трендового релиза на VPS. Вынесено из do_sync, потому что
# читается после каждого propose: три копии одного heredoc расходились бы молча.
latest_trend_release() {
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
        docker compose run --rm --entrypoint python3 reddit-compass -c \"
import sqlite3
conn = sqlite3.connect('/data/trend_engine.db')
print(conn.execute('SELECT trend_release_id FROM trend_releases ORDER BY created_at DESC LIMIT 1').fetchone()[0])
\" 2>/dev/null" 2>/dev/null
}

# Таблица типов акторов для глубины 3 схемного слоя Trends.
#
# Цикл идёт на VPS, а зависимость [actors] (GLiNER → torch) в прод-образ намеренно не
# входит: лимиты контейнера 4g/4cpu выстраданы под cross-encoder. Поэтому VPS отдаёт
# заголовки готового story-релиза, Mac считает по ним типы и кладёт таблицу обратно
# в /data. Возврат ≠ 0 — таблицы нет, и звать глубину 3 нельзя: без неё движок её
# всё равно понизит (ACTOR TYPING FALLBACK), а релиз выйдет под чужим именем.
sync_actor_types() {
    local story_id="$1"
    local titles_tmp types_tmp remote_types status=0
    echo "   Типизация акторов для глубины 3 (story=${story_id})..."
    titles_tmp="$(mktemp -t rc-titles)"
    types_tmp="$(mktemp -t rc-actor-types)"
    remote_types="/tmp/rc-actor-types-${TODAY}.json"
    if ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine actors export-titles \
                --story-release ${story_id} 2>/dev/null" > "${titles_tmp}" \
       && uv run --extra actors reddit-compass engine actors type \
                --titles "${titles_tmp}" --out "${types_tmp}"; then
        scp "${types_tmp}" "${VPS_USER}@${VPS_HOST}:${remote_types}"
        ssh "${VPS_USER}@${VPS_HOST}" \
            "docker cp ${remote_types} rc-api:/data/actor_types.json && rm -f ${remote_types}"
    else
        status=1
    fi
    rm -f "${titles_tmp}" "${types_tmp}"
    return "${status}"
}

do_sync() {
    echo ""
    echo "📦 [$(date +%H:%M:%S)] Синхронизация данных на VPS..."
    local local_snapshot="${DATA_DIR}/snapshots/${TODAY}/posts.jsonl"
    local remote_tmp="/tmp/rc-reddit-${TODAY}.jsonl"
    if [[ ! -s "${local_snapshot}" ]]; then
        echo "❌ Не найден непустой Reddit snapshot: ${local_snapshot}" >&2
        return 1
    fi

    echo "   Reddit JSONL → ${VPS_USER}@${VPS_HOST}:${remote_tmp} → Docker volume /data/snapshots/${TODAY}/"
    scp "${local_snapshot}" "${VPS_USER}@${VPS_HOST}:${remote_tmp}"
    ssh "${VPS_USER}@${VPS_HOST}" \
        "docker exec rc-api mkdir -p /data/snapshots/${TODAY} && \
         docker cp ${remote_tmp} rc-api:/data/snapshots/${TODAY}/posts.jsonl && \
         rm -f ${remote_tmp}"

    # Never copy a local compass.db: the VPS owns its raw corpus and combines
    # this Reddit artifact with the VPS adapters through `collect --from-snapshots`.
    echo "✅ Reddit artifact synced."

    # ── Trigger finalization + engine cycle on VPS ──────────────────────────
    echo ""
    echo "🔄 [$(date +%H:%M:%S)] Триггерю финализацию + Engine cycle на VPS..."
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
        docker compose run --rm reddit-compass collect --from-snapshots --date ${TODAY} --profile broad 2>&1 | tail -5"
    echo "   Finalization done. Running engine cycle..."
    # --cross-encoder обязателен, а не опционален. Без этой стадии серая зона Stories
    # остаётся неразобранной, и полы полноты не берутся: замер 3 августа дал
    # stories_multi_per_1k = 50.6 при поле 65 и compression 0.931 при потолке 0.90.
    # Прогон при этом публиковался на broad через --force, то есть гейт молчал, а
    # боевой канал жил с недособранными сюжетами. Лимиты контейнера (4g / 4 cpu,
    # OMP/MKL/TORCH_NUM_THREADS=4) выставлены ровно под эту стадию.
    # --trend-review-limit: ревью копит кэш llm_reviews и материализует confirmed в
    # релиз, без него /today (фильтр по confirmed) пуст. Корни ревьювятся топ-20
    # выборкой, пакет идёт с ограниченной параллельностью — минуты, не часы.
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
        docker compose run --rm reddit-compass engine cycle --cross-encoder --trend-review-limit 12 2>&1 | tail -5"
    echo "   Engine cycle done. Publishing..."

    # Read latest story/trend release IDs and publish
    local release_ids
    release_ids=$(ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
        docker compose run --rm --entrypoint python3 reddit-compass -c \"
import sqlite3, json
conn = sqlite3.connect('/data/trend_engine.db')
c = conn.cursor()
sr = c.execute('SELECT story_release_id FROM story_releases ORDER BY created_at DESC LIMIT 1').fetchone()
tr = c.execute('SELECT trend_release_id FROM trend_releases ORDER BY created_at DESC LIMIT 1').fetchone()
print(json.dumps({'story': sr[0], 'trend': tr[0]}))
\" 2>/dev/null" 2>/dev/null)
    local story_id trend_id
    story_id=$(echo "${release_ids}" | python3 -c "import sys,json; print(json.load(sys.stdin)['story'])")
    trend_id=$(echo "${release_ids}" | python3 -c "import sys,json; print(json.load(sys.stdin)['trend'])")

    # ── Типизация акторов: глубина 3 схемного слоя Trends ───────────────────
    # По умолчанию выключено: ночной прогон публикует на broad с --force, и включать
    # новую гранулярность без ревизии владельца нельзя. Включение — RC_TREND_DEPTH=3.
    if [[ "${RC_TREND_DEPTH:-2}" == "3" ]]; then
        if sync_actor_types "${story_id}"; then
            ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
                docker compose run --rm reddit-compass engine trends propose \
                    --story-release ${story_id} --method schema_v2 --trend-depth 3 2>&1 | tail -3"
            trend_id="$(latest_trend_release)"
            echo "   Глубина 3 собрана: trend=${trend_id}"
        else
            # Деградация — no-op: публикуется depth-2 релиз, собранный самим циклом.
            echo "⚠️  Типизация не удалась; остаёмся на релизе цикла (глубина 2)." >&2
        fi
    fi

    # ── Поколение 5: schema_v3 поверх кэша извлечения ───────────────────────
    # Кэш keyed по заголовкам, поэтому каждую ночь считается только дневная
    # дельта. Порядок важен: цикл сначала создаёт story-релиз, extract греет
    # кэш по нему, propose строит тренды поверх кэша, review + повторный
    # propose материализуют confirmed (иначе /today пуст).
    #
    # По умолчанию выключено. При включении публикация идёт на broad-preview
    # без --force: новое поколение не попадает на broad без ревизии владельца
    # и зелёного гейта. Включение — RC_TREND_METHOD=schema_v3.
    if [[ "${RC_TREND_METHOD:-embedding_v2}" == "schema_v3" ]]; then
        echo "   schema_v3: extract + normalize + propose + review (story=${story_id})..."
        # Глубину 3 просим только когда таблица типов действительно построена. Раньше
        # здесь стояло безусловное `--trend-depth 3`, а таблицу строил лишь блок выше
        # под другим флагом (RC_TREND_DEPTH), по умолчанию выключенным, — и прогон
        # каждую ночь молча шёл на глубине 2 под именем глубины 3.
        local v3_depth=2
        if [[ "${RC_TREND_DEPTH:-2}" == "3" ]] && sync_actor_types "${story_id}"; then
            v3_depth=3
        elif [[ "${RC_TREND_DEPTH:-2}" == "3" ]]; then
            echo "⚠️  Типизация не удалась; schema_v3 идёт на глубине 2." >&2
        fi
        ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine schemas extract \
                --story-release ${story_id} 2>&1 | tail -3"
        ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine schemas normalize-actors 2>&1 | tail -3"
        ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine trends propose \
                --story-release ${story_id} --method schema_v3 \
                --trend-depth ${v3_depth} 2>&1 | tail -3"
        trend_id="$(latest_trend_release)"
        ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine trends review \
                --trend-release ${trend_id} --limit 200 2>&1 | tail -3"
        # Ре-материализация: кэш ревью становится статусом confirmed в новом релизе.
        ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
            docker compose run --rm reddit-compass engine trends propose \
                --story-release ${story_id} --method schema_v3 \
                --trend-depth ${v3_depth} 2>&1 | tail -3"
        trend_id="$(latest_trend_release)"
        echo "   schema_v3 собран: trend=${trend_id} (глубина ${v3_depth})"
    fi

    local publish_channel="broad" publish_extra="--force"
    if [[ "${RC_TREND_METHOD:-embedding_v2}" == "schema_v3" ]]; then
        publish_channel="broad-preview"
        publish_extra=""
    fi

    echo "   Publishing: story=${story_id} trend=${trend_id} channel=${publish_channel}"
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && \
        docker compose run --rm reddit-compass engine publish \
            --story-release ${story_id} --trend-release ${trend_id} \
            --channel ${publish_channel} --allow-partial ${publish_extra} 2>&1 | tail -3"
    echo "✅ [$(date +%H:%M:%S)] VPS pipeline complete: collect → engine → publish."
}

# ── Main ───────────────────────────────────────────────────────────────────

case "${1:-all}" in
    --fetch)
        do_fetch
        ;;
    --sync)
        do_sync
        ;;
    all|*)
        do_fetch
        do_sync
        ;;
esac

echo ""
echo "🏁 [$(date +%H:%M:%S)] Готово."

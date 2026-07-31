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
    echo "✅ Reddit artifact synced. VPS finalizer will create the unified raw run."
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

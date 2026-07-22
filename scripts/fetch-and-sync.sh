#!/usr/bin/env bash
# fetch-and-sync.sh — Reddit fetch локально (Mac, residential IP) + sync на VPS.
#
# Запуск:
#   ./scripts/fetch-and-sync.sh          # полный цикл
#   ./scripts/fetch-and-sync.sh --fetch  # только fetch (без sync)
#   ./scripts/fetch-and-sync.sh --sync   # только sync (без fetch)
#
# Ночной автозапуск: scripts/com.reddit-compass.nightly.plist (launchd)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VPS_HOST="204.168.239.217"
VPS_USER="deploy"
REMOTE_DIR="/opt/reddit-compass"
DATA_DIR="${PROJECT_DIR}/data"
TODAY=$(date +%Y-%m-%d)

cd "${PROJECT_DIR}"

# ── Fetch (локально, Playwright, residential IP) ───────────────────────────

do_fetch() {
    echo "🔍 [$(date +%H:%M:%S)] Reddit fetch: ${TODAY}"
    echo "============================================================"
    uv run reddit-compass fetch --stealth
    echo ""
    echo "📡 [$(date +%H:%M:%S)] Hacker News..."
    uv run reddit-compass hn
    echo ""
    echo "📡 [$(date +%H:%M:%S)] RSS (BBC, Guardian, TechCrunch, Verge, Ars)..."
    uv run reddit-compass rss
    echo ""

    # LLM-анализ (если есть ключ)
    if [[ -n "${DASHSCOPE_API_KEY:-}" ]]; then
        echo "🤖 [$(date +%H:%M:%S)] LLM-анализ (Qwen API)..."
        uv run reddit-compass signals || echo "⚠️ signals failed (non-critical)"
    fi

    echo ""
    echo "✅ [$(date +%H:%M:%S)] Fetch завершён: ${DATA_DIR}/snapshots/${TODAY}/"
    ls -la "${DATA_DIR}/snapshots/${TODAY}/" 2>/dev/null || true
}

# ── Sync на VPS ────────────────────────────────────────────────────────────

do_sync() {
    echo ""
    echo "📦 [$(date +%H:%M:%S)] Синхронизация данных на VPS..."
    echo "   ${DATA_DIR}/snapshots/${TODAY}/ → ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/data/snapshots/${TODAY}/"

    # Создаём каталог на VPS
    ssh "${VPS_USER}@${VPS_HOST}" "mkdir -p ${REMOTE_DIR}/data/snapshots/${TODAY}"

    # Копируем snapshot
    scp -r "${DATA_DIR}/snapshots/${TODAY}/" \
        "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/data/snapshots/${TODAY}/"

    # Копируем SQLite (если есть)
    if [[ -f "${DATA_DIR}/compass.db" ]]; then
        scp "${DATA_DIR}/compass.db" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/data/compass.db"
    fi

    echo "✅ Sync завершён. API на VPS видит новые данные."
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

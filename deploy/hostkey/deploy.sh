#!/usr/bin/env bash
# deploy.sh — деплой reddit-compass на выделенный VPS.
# Запуск: ./deploy/hostkey/deploy.sh
#
# Требования:
#   - SSH-доступ: `${RC_DEPLOY_USER}@${RC_DEPLOY_HOST}` из .env.secrets
#     или SSH alias `reddit-compass-vps`
#   - Docker + docker compose на VPS
#   - .env.secrets заполнен (deploy/hostkey/.env.secrets)
#
# Что делает:
#   1. Копирует compose + Caddyfile + .env.secrets на VPS
#   2. Создаёт /opt/reddit-compass/
#   3. Запускает api + caddy
#   4. (Опционально) устанавливает version-controlled host-cron

set -euo pipefail

REMOTE_DIR="/opt/reddit-compass"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SECRETS_FILE="${SCRIPT_DIR}/.env.secrets"

if [[ ! -f "${SECRETS_FILE}" ]]; then
    echo "❌ .env.secrets не найден. Создайте из .env.secrets.example"
    exit 1
fi

set -a
# shellcheck source=/dev/null
. "${SECRETS_FILE}"
set +a

VPS_HOST="${RC_DEPLOY_HOST:-reddit-compass-vps}"
VPS_USER="${RC_DEPLOY_USER:-deploy}"
PUBLIC_BASE_URL="${RC_PUBLIC_BASE_URL:-http://localhost:8900}"

echo "🚀 Деплой reddit-compass на ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}"

echo "📁 Создаю ${REMOTE_DIR} на VPS..."
ssh "${VPS_USER}@${VPS_HOST}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown ${VPS_USER}:${VPS_USER} ${REMOTE_DIR}"

echo "📦 Копирую исходники + compose + Caddyfile + secrets..."
ssh "${VPS_USER}@${VPS_HOST}" "mkdir -p ${REMOTE_DIR}/src ${REMOTE_DIR}/config"
scp "${SCRIPT_DIR}/docker-compose.yml" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/Caddyfile" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/Dockerfile.api" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/reddit-compass.cron" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/install-cron.sh" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SECRETS_FILE}" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/.env"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
scp "${PROJECT_ROOT}/Dockerfile" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${PROJECT_ROOT}/pyproject.toml" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${PROJECT_ROOT}/README.md" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp -r "${PROJECT_ROOT}/src/reddit_compass" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/src/"
scp -r "${PROJECT_ROOT}/config/." "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/config/"

echo "🐳 Собираю образы api + collector (первый раз — долго: базовый Playwright)..."
ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && docker compose build api reddit-compass"

echo "🐳 Запускаю api + caddy..."
ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && docker compose up -d api caddy"

echo "🔄 Рестарт Caddy (Docker DNS refresh)..."
ssh "${VPS_USER}@${VPS_HOST}" "docker restart rc-caddy"

if [[ "${INSTALL_CRON:-0}" == "1" ]]; then
    echo "⏱️  Устанавливаю managed host-cron..."
    ssh "${VPS_USER}@${VPS_HOST}" "chmod +x ${REMOTE_DIR}/install-cron.sh && ${REMOTE_DIR}/install-cron.sh"
fi

echo "🏥 Проверяю health..."
sleep 3
ssh "${VPS_USER}@${VPS_HOST}" "curl -s http://127.0.0.1:8900/health" && echo ""

echo ""
echo "✅ Деплой завершён!"
echo "   API: ${PUBLIC_BASE_URL}/health"
echo "   Today: ${PUBLIC_BASE_URL}/today"
echo "   Docs: ${PUBLIC_BASE_URL}/docs"
echo ""
echo "📋 Для version-controlled nightly cron:"
echo "   INSTALL_CRON=1 ./deploy/hostkey/deploy.sh"

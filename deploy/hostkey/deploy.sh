#!/usr/bin/env bash
# deploy.sh — деплой reddit-compass на VPS HostKey «Hermes».
# Запуск: ./deploy/hostkey/deploy.sh
#
# Требования:
#   - SSH-доступ: deploy@204.168.239.217 (ключ ~/.ssh/id_rsa)
#   - Docker + docker compose на VPS
#   - .env.secrets заполнен (deploy/hostkey/.env.secrets)
#
# Что делает:
#   1. Копирует compose + Caddyfile + .env.secrets на VPS
#   2. Создаёт /opt/reddit-compass/
#   3. Запускает api + caddy
#   4. (Опционально) добавляет host-cron для nightly

set -euo pipefail

VPS_HOST="204.168.239.217"
VPS_USER="deploy"
REMOTE_DIR="/opt/reddit-compass"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Деплой reddit-compass на ${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}"
echo "============================================================"

# Проверка: .env.secrets существует
if [[ ! -f "${SCRIPT_DIR}/.env.secrets" ]]; then
    echo "❌ .env.secrets не найден. Создайте из .env.secrets.example"
    exit 1
fi

# 1. Создаём каталог на VPS
echo "📁 Создаю ${REMOTE_DIR} на VPS..."
ssh "${VPS_USER}@${VPS_HOST}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown ${VPS_USER}:${VPS_USER} ${REMOTE_DIR}"

# 2. Копируем файлы (build context + compose + secrets)
echo "📦 Копирую исходники + compose + Caddyfile + secrets..."
ssh "${VPS_USER}@${VPS_HOST}" "mkdir -p ${REMOTE_DIR}/src ${REMOTE_DIR}/config"
scp "${SCRIPT_DIR}/docker-compose.yml" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/Caddyfile" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/Dockerfile.api" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${SCRIPT_DIR}/.env.secrets" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/.env"
# Build context для Docker
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
scp "${PROJECT_ROOT}/Dockerfile" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${PROJECT_ROOT}/pyproject.toml" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp "${PROJECT_ROOT}/README.md" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/"
scp -r "${PROJECT_ROOT}/src/reddit_compass" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/src/"
scp -r "${PROJECT_ROOT}/config/profiles" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/config/"

# 3. Запускаем сервисы
echo "🐳 Запускаю api + caddy..."
ssh "${VPS_USER}@${VPS_HOST}" "cd ${REMOTE_DIR} && docker compose up -d api caddy"

# 4. Проверяем health
echo "🏥 Проверяю health..."
sleep 3
ssh "${VPS_USER}@${VPS_HOST}" "curl -s http://127.0.0.1:8900/health" && echo ""

echo ""
echo "✅ Деплой завершён!"
echo "   API: http://${VPS_HOST}:8900 (loopback)"
echo "   Dashboard: http://${VPS_HOST}:8900/dashboard"
echo "   Docs: http://${VPS_HOST}:8900/docs"
echo ""
echo "📋 Для nightly cron (на VPS):"
echo "   crontab -e → добавить:"
echo "   17 3 * * * cd ${REMOTE_DIR} && docker compose run --rm reddit-compass nightly >> /var/log/reddit-compass/nightly.log 2>&1"

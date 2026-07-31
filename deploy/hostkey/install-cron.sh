#!/usr/bin/env bash
# Installs only reddit-compass's managed host-cron block on the VPS.
# Other applications' cron entries are preserved verbatim.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/reddit-compass.cron"
CURRENT="$(mktemp)"
NEXT="$(mktemp)"
trap 'rm -f "${CURRENT}" "${NEXT}"' EXIT

if [[ ! -f "${TEMPLATE}" ]]; then
    echo "Missing cron template: ${TEMPLATE}" >&2
    exit 1
fi

(crontab -l 2>/dev/null || true) > "${CURRENT}"

# Remove a previous managed block and only the six known legacy reddit-compass
# commands. The filter is deliberately scoped to /opt/reddit-compass.
awk '
    /^# BEGIN reddit-compass managed pipeline$/ { in_block = 1; next }
    /^# END reddit-compass managed pipeline$/ { in_block = 0; next }
    in_block { next }
    $0 ~ /\/opt\/reddit-compass/ &&
      $0 ~ /reddit-compass (rss|hn|ladder|ph|signals|radar|engine cycle|collect --from-snapshots)/ { next }
    { print }
' "${CURRENT}" > "${NEXT}"

if [[ -s "${NEXT}" ]] && [[ -n "$(tail -c 1 "${NEXT}")" ]]; then
    printf '\n' >> "${NEXT}"
fi
cat "${TEMPLATE}" >> "${NEXT}"
crontab "${NEXT}"
echo "Installed managed reddit-compass collection/Engine cron block."

#!/usr/bin/env bash
# Вердикт о прошедшем ночном прогоне — одной строкой в durable-журнал.
#
# Зачем: конвейер из семи стадий по крону отказывает молча. Контейнер продолжает
# отвечать `ok`, UI показывает вчерашний выпуск, а стадии пишут каждая в свой лог,
# который никто не открывает. До этой проверки узнать о простое можно было, только
# заметив старую дату на странице.
#
# Проверка сознательно тупая и без сети наружу: свежесть данных из /health и покрытие
# сбора из самого сервиса. Она отвечает на один вопрос — «прошлой ночью конвейер довёл
# работу до публикации или нет».
#
# Код возврата 1 при неудаче: это точка, к которой позже цепляется внешний монитор или
# отправка уведомления, без переписывания самой проверки.

set -uo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/reddit-compass}"
LOG_DIR="${RC_LOG_DIR:-${REMOTE_DIR}/logs}"
VERDICT_LOG="${LOG_DIR}/verdict.log"
mkdir -p "${LOG_DIR}"

stamp="$(date -u '+%Y-%m-%d %H:%M UTC')"
problems=()

health_json="$(curl -s --max-time 20 http://127.0.0.1:8900/health || echo '')"
if [[ -z "${health_json}" ]]; then
  health_status="unreachable"
  data_status="unknown"
  age="?"
  problems+=("сервис не отвечает")
else
  health_status="$(printf '%s' "${health_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null || echo '?')"
  data_status="$(printf '%s' "${health_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data_status","?"))' 2>/dev/null || echo '?')"
  age="$(printf '%s' "${health_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("age_hours","?"))' 2>/dev/null || echo '?')"
  [[ "${data_status}" == "stale" ]] && problems+=("данные устарели: ${age} ч без публикации")
fi

# Окно обязательно: без `--since/--until` команда не считает покрытие, а завершается
# ошибкой использования. Первый прогон этой проверки поймал именно это — и отчитался
# «пропущенные дни» там, где сломана была сама проверка. Разница принципиальна: красный
# сигнал, горящий каждую ночь по одной и той же причине, перестают читать за неделю.
until_day="$(date -u +%F)"
since_day="$(date -u -d '6 days ago' +%F 2>/dev/null || date -u -v-6d +%F)"
coverage_out="$(cd "${REMOTE_DIR}" && docker compose run --rm reddit-compass collect \
  --coverage --since "${since_day}" --until "${until_day}" --fail-on-gap 2>&1)"
coverage_code=$?
if printf '%s' "${coverage_out}" | grep -q '"summary"'; then
  # Команда отработала: код возврата означает именно наличие пропусков.
  if [[ ${coverage_code} -ne 0 ]]; then
    coverage_status="gap"
    problems+=("покрытие ${since_day}…${until_day}: есть пропущенные дни")
  else
    coverage_status="ok"
  fi
else
  coverage_status="сломана"
  problems+=("проверка покрытия не отработала")
fi
printf '%s\n' "${coverage_out}" >> "${LOG_DIR}/coverage.log"

# Выпуск, отклонённый гейтом, до этой проверки был невидим: shadow публикуется всегда,
# поэтому конвейер выглядел успешным, возраст данных не дотягивал до 36-часового порога
# `/health`, и вердикт писал OK — при том что на сайте оставался вчерашний выпуск.
# Автоматическое продвижение сделало этот разрыв возможным, значит он обязан звучать.
# Проверка идёт после Engine, поэтому к этому моменту на сайте обязан стоять СЕГОДНЯШНИЙ
# выпуск. Сравнивать shadow с broad недостаточно: 9 августа цикл не опубликовал вообще
# ничего (полы отклонили выпуск), даты обоих каналов остались вчерашними и равными — а
# проверка на «shadow новее broad» промолчала. Вопрос надо задавать прямо.
promo="$(cd "${REMOTE_DIR}" && docker compose run --rm --entrypoint python reddit-compass -c "
import sqlite3
from datetime import UTC, datetime
c = sqlite3.connect('file:/data/trend_engine.db?mode=ro', uri=True)
row = c.execute(
    \"SELECT MAX(created_at) FROM radar_publications WHERE channel = 'broad'\"
).fetchone()
latest = (row[0] or '')[:10]
today = datetime.now(UTC).date().isoformat()
print('ok' if latest == today else f'stale:{latest or \"нет публикаций\"}')
" 2>/dev/null | tail -1)"
case "${promo}" in
  ok) ;;
  stale:*) problems+=("на сайте не сегодняшний выпуск (${promo#stale:}) — выпуск не прошёл гейт") ;;
  *)  problems+=("не удалось проверить публикацию на сайте") ;;
esac

if [[ ${#problems[@]} -eq 0 ]]; then
  verdict="OK"
else
  verdict="ПРОБЛЕМА: $(IFS='; '; echo "${problems[*]}")"
fi

echo "${stamp} | health=${health_status} data=${data_status} age=${age}ч | coverage=${coverage_status} | ${verdict}" \
  | tee -a "${VERDICT_LOG}"

# Журнал вердиктов растёт одной строкой в сутки, но обрезается на всякий случай:
# незамеченный рост файла — классическая причина заполнить диск за год.
tail -n 400 "${VERDICT_LOG}" > "${VERDICT_LOG}.tmp" && mv "${VERDICT_LOG}.tmp" "${VERDICT_LOG}"

[[ ${#problems[@]} -eq 0 ]]

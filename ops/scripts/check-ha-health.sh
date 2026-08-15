#!/usr/bin/env bash
# ===========================================================================
#  Health-check: Home Assistant (в VM) + Frigate (в Docker)
#
#  Домашняя система без дежурного администратора. Контейнер падает,
#  VM зависает, камера отваливается — это не гипотетические сценарии.
#  Скрипт поднимает упавшее без участия человека.
#
#  Установка:
#      sudo cp check-ha-health.sh /usr/local/bin/
#      sudo chmod +x /usr/local/bin/check-ha-health.sh
#      crontab -e     # ← ОБЫЧНОГО пользователя, НЕ root
#      */5 * * * * /usr/local/bin/check-ha-health.sh >> ~/ha-health.log 2>&1
#
#  ⚠️ ПОЧЕМУ НЕ ОТ ROOT: виртуалка VirtualBox принадлежит обычному
#  пользователю. Из-под root `VBoxManage list vms` её просто не увидит,
#  и скрипт будет молча ничего не делать.
# ===========================================================================
set -uo pipefail

# --- Настройки -------------------------------------------------------------
HA_URL="${HA_URL:-http://HA_VM_IP:8123}"
FRIGATE_URL="${FRIGATE_URL:-http://127.0.0.1:5000}"
VM_NAME="${VM_NAME:-homeassistant}"
FRIGATE_DIR="${FRIGATE_DIR:-$HOME/frigate}"

CURL_TIMEOUT=10
# Дать сервису подняться после рестарта, прежде чем считать его мёртвым
GRACE_AFTER_RESTART=90

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

is_up() {
  curl --silent --fail --max-time "${CURL_TIMEOUT}" --output /dev/null "$1"
}

# --- Home Assistant --------------------------------------------------------
check_home_assistant() {
  if is_up "${HA_URL}"; then
    return 0
  fi

  log "HA не отвечает на ${HA_URL}"

  if ! command -v VBoxManage >/dev/null 2>&1; then
    log "  VBoxManage не найден — пропускаю"
    return 1
  fi

  local state
  state="$(VBoxManage showvminfo "${VM_NAME}" --machinereadable 2>/dev/null \
           | grep '^VMState=' | cut -d'"' -f2)"

  if [[ -z "${state}" ]]; then
    log "  VM '${VM_NAME}' не найдена. Скрипт запущен от того пользователя, которому принадлежит VM?"
    return 1
  fi

  log "  состояние VM: ${state}"

  case "${state}" in
    running)
      # VM жива, но HA внутри не отвечает — жёсткий перезапуск
      log "  VM работает, но HA молчит -> перезапускаю VM"
      VBoxManage controlvm "${VM_NAME}" poweroff 2>/dev/null || true
      sleep 5
      ;;
    *)
      log "  VM не запущена -> стартую"
      ;;
  esac

  VBoxManage startvm "${VM_NAME}" --type headless
  log "  жду ${GRACE_AFTER_RESTART} с..."
  sleep "${GRACE_AFTER_RESTART}"

  if is_up "${HA_URL}"; then
    log "  HA поднялся"
  else
    log "  HA всё ещё не отвечает — нужно смотреть руками"
  fi
}

# --- Frigate ---------------------------------------------------------------
check_frigate() {
  if is_up "${FRIGATE_URL}/api/version"; then
    return 0
  fi

  log "Frigate не отвечает на ${FRIGATE_URL}"

  if [[ ! -d "${FRIGATE_DIR}" ]]; then
    log "  каталог ${FRIGATE_DIR} не найден — пропускаю"
    return 1
  fi

  log "  перезапускаю контейнер"
  ( cd "${FRIGATE_DIR}" && docker compose restart frigate )

  sleep 30

  if is_up "${FRIGATE_URL}/api/version"; then
    log "  Frigate поднялся"
  else
    log "  Frigate не поднялся — смотри: docker compose logs frigate"
  fi
}

# --- Запуск ----------------------------------------------------------------
check_home_assistant
check_frigate

#!/bin/bash
set -Eeuo pipefail

source /etc/ha-pxe/bootstrap.env

HA_PXE_CLIENT_LOG_PREFIX="ha-pxe-early-log"
HA_PXE_CLIENT_LOG_SOURCE="earlyboot"
source /usr/local/lib/ha-pxe/client-log.sh

MARKER_FILE="/var/lib/ha-pxe/firstboot.done"

trap 'ha_pxe_client::err_trap "$?" "${LINENO}"' ERR

log_info() {
  ha_pxe_client::log info "${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-startup}" in_progress "$*"
}

route_summary() {
  local route=""

  route="$(ip -4 route show default 2>/dev/null | head -n 1 || true)"
  if [[ -z "${route}" ]]; then
    route="none"
  fi

  printf '%s\n' "${route}"
}

address_summary() {
  local addresses=""

  addresses="$(
    ip -4 -brief address show up 2>/dev/null \
      | awk '{print $1 "=" $3}' \
      | paste -sd ';' -
  )"
  if [[ -z "${addresses}" ]]; then
    addresses="none"
  fi

  printf '%s\n' "${addresses}"
}

unit_summary() {
  local unit="${1}"
  local load_state active_state sub_state unit_file_state

  load_state="$(systemctl show -P LoadState "${unit}" 2>/dev/null || true)"
  active_state="$(systemctl show -P ActiveState "${unit}" 2>/dev/null || true)"
  sub_state="$(systemctl show -P SubState "${unit}" 2>/dev/null || true)"
  unit_file_state="$(systemctl show -P UnitFileState "${unit}" 2>/dev/null || true)"

  printf '%s load=%s active=%s sub=%s enabled=%s\n' \
    "${unit}" \
    "${load_state:-unknown}" \
    "${active_state:-unknown}" \
    "${sub_state:-unknown}" \
    "${unit_file_state:-unknown}"
}

wait_for_transport() {
  local attempt=1
  local max_attempts=12

  while (( attempt <= max_attempts )); do
    if ha_pxe_client::emit_remote info startup reachable "Early boot diagnostics reached the add-on log transport on attempt ${attempt}"; then
      return 0
    fi

    sleep 5
    attempt=$((attempt + 1))
  done

  return 1
}

main() {
  local boot_id marker_present docker_present route ipv4_summary

  ha_pxe_client::emit_local info startup started "Starting early boot diagnostics for ${PXE_HOSTNAME} (${PXE_SERIAL})"
  wait_for_transport || true

  ha_pxe_client::stage_start startup "Collecting early boot diagnostics for ${PXE_HOSTNAME} (${PXE_SERIAL})"

  boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf 'unknown\n')"
  route="$(route_summary)"
  ipv4_summary="$(address_summary)"

  marker_present="no"
  if [[ -f "${MARKER_FILE}" ]]; then
    marker_present="yes"
  fi

  docker_present="no"
  if command -v docker >/dev/null 2>&1; then
    docker_present="yes"
  fi

  log_info "boot_id=${boot_id} marker_present=${marker_present} docker_present=${docker_present}"
  log_info "default_route=${route}"
  log_info "ipv4_addresses=${ipv4_summary}"
  log_info "$(unit_summary network.target)"
  log_info "$(unit_summary network-online.target)"
  log_info "$(unit_summary ha-pxe-early-log.service)"
  log_info "$(unit_summary ha-pxe-firstboot.service)"
  log_info "$(unit_summary ha-pxe-container-sync.service)"
  log_info "$(unit_summary ha-pxe-container-sync.timer)"
  ha_pxe_client::stage_complete startup "Early boot diagnostics captured"
}

main "$@"

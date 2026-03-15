#!/bin/bash

HA_PXE_CLIENT_LOG_PREFIX="${HA_PXE_CLIENT_LOG_PREFIX:-ha-pxe-client}"
HA_PXE_CLIENT_LOG_SOURCE="${HA_PXE_CLIENT_LOG_SOURCE:-client}"
HA_PXE_CLIENT_LOG_CURRENT_STAGE="${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-startup}"
HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED="${HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED:-false}"

ha_pxe_client::sanitize_token() {
  local value="${1:-unknown}"

  value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  value="$(sed -E 's/[^a-z0-9_.-]+/-/g; s/^-+//; s/-+$//' <<<"${value}")"
  if [[ -z "${value}" ]]; then
    value="unknown"
  fi

  printf '%s\n' "${value}"
}

ha_pxe_client::sanitize_message() {
  local value="${1:-}"

  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="$(sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' <<<"${value}")"

  printf '%s\n' "${value}"
}

ha_pxe_client::emit_local() {
  local level="${1}"
  local stage="${2}"
  local status="${3}"
  local message="${4}"

  printf '[%s] level=%s stage=%s status=%s %s\n' \
    "${HA_PXE_CLIENT_LOG_PREFIX}" \
    "${level}" \
    "${stage}" \
    "${status}" \
    "${message}" >&2
}

ha_pxe_client::emit_remote() {
  local level="${1}"
  local stage="${2}"
  local status="${3}"
  local message="${4}"
  local exit_code="${5:-}"
  local body content_length request

  [[ -n "${PXE_LOG_HOST:-}" && -n "${PXE_LOG_PORT:-}" && -n "${PXE_LOG_PATH:-}" ]] || return 0

  body="$(ha_pxe_client::sanitize_message "${message}")"
  content_length="$(printf '%s' "${body}" | wc -c | tr -d '[:space:]')"
  request="$(
    printf 'POST %s HTTP/1.1\r\n' "${PXE_LOG_PATH}"
    printf 'Host: %s:%s\r\n' "${PXE_LOG_HOST}" "${PXE_LOG_PORT}"
    printf 'Connection: close\r\n'
    printf 'Content-Type: text/plain; charset=utf-8\r\n'
    printf 'Content-Length: %s\r\n' "${content_length}"
    printf 'X-Ha-Pxe-Source: %s\r\n' "${HA_PXE_CLIENT_LOG_SOURCE}"
    printf 'X-Ha-Pxe-Level: %s\r\n' "${level}"
    printf 'X-Ha-Pxe-Stage: %s\r\n' "${stage}"
    printf 'X-Ha-Pxe-Status: %s\r\n' "${status}"
    printf 'X-Ha-Pxe-Hostname: %s\r\n' "${PXE_HOSTNAME:-}"
    printf 'X-Ha-Pxe-Serial: %s\r\n' "${PXE_SERIAL:-}"
    if [[ -n "${exit_code}" ]]; then
      printf 'X-Ha-Pxe-Exit-Code: %s\r\n' "${exit_code}"
    fi
    printf '\r\n'
    printf '%s' "${body}"
  )"

  if command -v timeout >/dev/null 2>&1; then
    if timeout 3 bash -c '
      exec 9<>"/dev/tcp/$1/$2" || exit 1
      printf "%s" "$3" >&9
      IFS= read -r status_line <&9 || exit 1
      case "$status_line" in
        HTTP/*" 204 "*|HTTP/*" 200 "*) ;;
        *) exit 1 ;;
      esac
      cat <&9 >/dev/null || true
      exec 9<&-
      exec 9>&-
    ' _ "${PXE_LOG_HOST}" "${PXE_LOG_PORT}" "${request}" >/dev/null 2>&1; then
      HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED="false"
      return 0
    fi
  else
    if bash -c '
      exec 9<>"/dev/tcp/$1/$2" || exit 1
      printf "%s" "$3" >&9
      IFS= read -r status_line <&9 || exit 1
      case "$status_line" in
        HTTP/*" 204 "*|HTTP/*" 200 "*) ;;
        *) exit 1 ;;
      esac
      cat <&9 >/dev/null || true
      exec 9<&-
      exec 9>&-
    ' _ "${PXE_LOG_HOST}" "${PXE_LOG_PORT}" "${request}" >/dev/null 2>&1; then
      HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED="false"
      return 0
    fi
  fi

  if [[ "${HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED}" != "true" ]]; then
    printf '[%s] level=warn stage=transport status=degraded Unable to reach add-on log transport at %s:%s%s\n' \
      "${HA_PXE_CLIENT_LOG_PREFIX}" \
      "${PXE_LOG_HOST}" \
      "${PXE_LOG_PORT}" \
      "${PXE_LOG_PATH}" >&2
    HA_PXE_CLIENT_REMOTE_LOG_FAILURE_REPORTED="true"
  fi

  return 1
}

ha_pxe_client::log() {
  local level stage status message exit_code clean_message

  level="$(ha_pxe_client::sanitize_token "${1:-info}")"
  stage="$(ha_pxe_client::sanitize_token "${2:-${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-unknown}}")"
  status="$(ha_pxe_client::sanitize_token "${3:-message}")"
  message="${4:-No details provided}"
  exit_code="${5:-}"
  clean_message="$(ha_pxe_client::sanitize_message "${message}")"

  HA_PXE_CLIENT_LOG_CURRENT_STAGE="${stage}"
  ha_pxe_client::emit_local "${level}" "${stage}" "${status}" "${clean_message}"
  ha_pxe_client::emit_remote "${level}" "${stage}" "${status}" "${clean_message}" "${exit_code}" || true
}

ha_pxe_client::stage_start() {
  local stage="${1}"
  local message="${2}"

  HA_PXE_CLIENT_LOG_CURRENT_STAGE="$(ha_pxe_client::sanitize_token "${stage}")"
  ha_pxe_client::log info "${HA_PXE_CLIENT_LOG_CURRENT_STAGE}" started "${message}"
}

ha_pxe_client::stage_complete() {
  local stage="${1:-${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-unknown}}"
  local message="${2}"

  stage="$(ha_pxe_client::sanitize_token "${stage}")"
  HA_PXE_CLIENT_LOG_CURRENT_STAGE="${stage}"
  ha_pxe_client::log info "${stage}" completed "${message}"
}

ha_pxe_client::stage_skip() {
  local stage="${1:-${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-unknown}}"
  local message="${2}"

  stage="$(ha_pxe_client::sanitize_token "${stage}")"
  HA_PXE_CLIENT_LOG_CURRENT_STAGE="${stage}"
  ha_pxe_client::log info "${stage}" skipped "${message}"
}

ha_pxe_client::stage_fail() {
  local stage="${1:-${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-unknown}}"
  local message="${2}"
  local exit_code="${3:-}"

  stage="$(ha_pxe_client::sanitize_token "${stage}")"
  HA_PXE_CLIENT_LOG_CURRENT_STAGE="${stage}"
  ha_pxe_client::log error "${stage}" failed "${message}" "${exit_code}"
}

ha_pxe_client::err_trap() {
  local exit_code="${1}"
  local line_no="${2}"
  local stage="${HA_PXE_CLIENT_LOG_CURRENT_STAGE:-unknown}"

  ha_pxe_client::stage_fail "${stage}" "Script failed at line ${line_no}" "${exit_code}"
}

#!/usr/bin/env bash
set -Eeuo pipefail

sanitize_token() {
  local value="${1:-unknown}"
  local header_key=""

  value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  value="$(sed -E 's/[^a-z0-9_.-]+/-/g; s/^-+//; s/-+$//' <<<"${value}")"
  if [[ -z "${value}" ]]; then
    value="unknown"
  fi

  printf '%s\n' "${value}"
}

sanitize_text() {
  local value="${1:-}"

  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="$(sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' <<<"${value}")"

  printf '%s\n' "${value}"
}

send_response() {
  local status="${1}"

  printf 'HTTP/1.1 %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n' "${status}"
}

emit_log() {
  local level="${1}"
  shift

  printf '[ha-pxe-client-transport] level=%s %s\n' "${level}" "$*" >&2
}

main() {
  local request_line method path protocol line
  local header_name header_value content_length=0
  local source="client" level="info" stage="unknown" status="message"
  local hostname="" serial="" exit_code="" body="" message=""

  if ! IFS= read -r request_line; then
    exit 0
  fi

  request_line="${request_line%$'\r'}"
  method="${request_line%% *}"
  path="${request_line#* }"
  path="${path%% *}"
  protocol="${request_line##* }"

  if [[ "${method}" != "POST" ]]; then
    send_response "405 Method Not Allowed"
    exit 0
  fi

  if [[ "${path}" != "/client-log" ]]; then
    send_response "404 Not Found"
    exit 0
  fi

  if [[ "${protocol}" != HTTP/* ]]; then
    send_response "400 Bad Request"
    exit 0
  fi

  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ -z "${line}" ]] && break

    header_name="${line%%:*}"
    if [[ "${header_name}" == "${line}" ]]; then
      continue
    fi

    header_value="${line#*:}"
    header_value="${header_value#"${header_value%%[![:space:]]*}"}"

    header_key="$(printf '%s' "${header_name}" | tr '[:upper:]' '[:lower:]')"
    case "${header_key}" in
      content-length)
        content_length="$(sanitize_text "${header_value}")"
        ;;
      x-ha-pxe-source)
        source="$(sanitize_token "${header_value}")"
        ;;
      x-ha-pxe-level)
        level="$(sanitize_token "${header_value}")"
        ;;
      x-ha-pxe-stage)
        stage="$(sanitize_token "${header_value}")"
        ;;
      x-ha-pxe-status)
        status="$(sanitize_token "${header_value}")"
        ;;
      x-ha-pxe-hostname)
        hostname="$(sanitize_text "${header_value}")"
        ;;
      x-ha-pxe-serial)
        serial="$(sanitize_text "${header_value}")"
        ;;
      x-ha-pxe-exit-code)
        exit_code="$(sanitize_text "${header_value}")"
        ;;
    esac
  done

  if [[ ! "${content_length}" =~ ^[0-9]+$ ]]; then
    send_response "400 Bad Request"
    exit 0
  fi

  if (( content_length > 0 )); then
    body="$(dd bs=1 count="${content_length}" status=none 2>/dev/null || true)"
  fi

  message="$(sanitize_text "${body}")"
  if [[ -z "${message}" ]]; then
    message="No message provided"
  fi

  send_response "204 No Content"
  emit_log "${level}" "source=${source} host=${hostname:-unknown} serial=${serial:-unknown} stage=${stage} status=${status}${exit_code:+ exit=${exit_code}} ${message}"
}

main "$@"

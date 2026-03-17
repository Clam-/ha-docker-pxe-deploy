#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HA_READY_TIMEOUT="${HA_DEV_WAIT_SECONDS:-300}"


die() {
  echo "Error: $*" >&2
  exit 1
}


usage() {
  cat <<'EOF'
Usage: bash scripts/ha-dev.sh <command>

Commands:
  info       Show the detected add-on slug and harness paths
  slug       Print the detected local add-on slug
  wait       Wait for the Home Assistant supervisor to become ready
  install    Reload the local store and install the add-on if needed
  configure  Apply the dev options payload and disable protection mode
  prepare    Install, configure, rebuild, and show add-on status
  rebuild    Force-rebuild the local add-on image
  start      Start the local add-on
  restart    Restart the local add-on
  status     Show the current add-on status
  logs       Follow the local add-on logs
EOF
}


discover_addon_dir() {
  if [[ -n "${HA_DEV_ADDON_DIR:-}" ]]; then
    local configured_dir

    if [[ "${HA_DEV_ADDON_DIR}" = /* ]]; then
      configured_dir="${HA_DEV_ADDON_DIR}"
    else
      configured_dir="${ROOT_DIR}/${HA_DEV_ADDON_DIR}"
    fi
    [[ -f "${configured_dir}/config.yaml" ]] || die "HA_DEV_ADDON_DIR must point at an add-on directory containing config.yaml"
    printf '%s\n' "${configured_dir}"
    return
  fi

  local addon_dirs=()
  local config_file
  while IFS= read -r config_file; do
    addon_dirs+=("$(dirname "${config_file}")")
  done < <(find "${ROOT_DIR}" -mindepth 2 -maxdepth 2 -name config.yaml -print | sort)

  case "${#addon_dirs[@]}" in
    1)
      printf '%s\n' "${addon_dirs[0]}"
      ;;
    0)
      die "No add-on directories were found under the repository root"
      ;;
    *)
      die "Multiple add-ons were found. Set HA_DEV_ADDON_DIR to the directory you want to target."
      ;;
  esac
}


read_addon_slug() {
  local addon_dir="$1"
  local slug

  slug="$(sed -n 's/^slug:[[:space:]]*//p' "${addon_dir}/config.yaml" | head -n 1 | tr -d '"' | tr -d "'")"
  [[ -n "${slug}" ]] || die "Unable to read the add-on slug from ${addon_dir}/config.yaml"
  printf '%s\n' "${slug}"
}


resolve_options_file() {
  if [[ -n "${HA_DEV_OPTIONS_FILE:-}" ]]; then
    [[ -f "${HA_DEV_OPTIONS_FILE}" ]] || die "HA_DEV_OPTIONS_FILE points to a missing file: ${HA_DEV_OPTIONS_FILE}"
    printf '%s\n' "${HA_DEV_OPTIONS_FILE}"
    return
  fi

  local base="${ROOT_DIR}/.devcontainer/${ADDON_SLUG}.options"
  if [[ -f "${base}.local.json" ]]; then
    printf '%s\n' "${base}.local.json"
    return
  fi
  if [[ -f "${base}.json" ]]; then
    printf '%s\n' "${base}.json"
    return
  fi

  die "Missing dev options file. Create ${base}.json or set HA_DEV_OPTIONS_FILE."
}


init_context() {
  ADDON_DIR="$(discover_addon_dir)"
  ADDON_SLUG="$(read_addon_slug "${ADDON_DIR}")"
  LOCAL_ADDON_SLUG="local_${ADDON_SLUG}"
  OPTIONS_FILE="$(resolve_options_file)"
}


require_tool() {
  local tool="$1"
  command -v "${tool}" >/dev/null 2>&1 || die "Required tool '${tool}' is not available in this shell"
}


require_ha_cli() {
  require_tool ha
}


ha_ready() {
  ha info --raw-json >/dev/null 2>&1
}


wait_for_ha() {
  require_ha_cli

  local elapsed=0
  while ! ha_ready; do
    if (( elapsed == 0 )); then
      echo "Waiting for Home Assistant to come up..." >&2
    fi
    if (( elapsed >= HA_READY_TIMEOUT )); then
      die "Home Assistant did not become ready within ${HA_READY_TIMEOUT}s. Run the 'Start Home Assistant' task first."
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
}


addon_installed() {
  ha apps info "${LOCAL_ADDON_SLUG}" --raw-json >/dev/null 2>&1
}


container_env() {
  local container="$1"
  local env_name="$2"
  local env_dump=""

  env_dump="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" 2>/dev/null || true)"
  printf '%s\n' "${env_dump}" | sed -n "s/^${env_name}=//p" | head -n 1
}


supervisor_ip() {
  local ip
  local inspect_json="[]"

  inspect_json="$(docker inspect "${HA_DEV_SUPERVISOR_CONTAINER:-hassio_supervisor}" 2>/dev/null || printf '[]\n')"
  ip="$(printf '%s\n' "${inspect_json}" \
    | jq -r '.[0].NetworkSettings.Networks // {} | to_entries[]? | .value.IPAddress' \
    | awk 'NF { print; exit }')"

  [[ -n "${ip}" ]] || die "Unable to determine the Home Assistant supervisor container IP address"
  printf '%s\n' "${ip}"
}


supervisor_token() {
  local token=""
  local lookup

  for lookup in \
    "hassio_cli:SUPERVISOR_API_TOKEN" \
    "hassio_cli:SUPERVISOR_TOKEN" \
    "hassio_supervisor:SUPERVISOR_API_TOKEN" \
    "hassio_supervisor:SUPERVISOR_TOKEN"; do
    IFS=":" read -r container env_name <<<"${lookup}"
    token="$(container_env "${container}" "${env_name}")"
    if [[ -n "${token}" ]]; then
      printf '%s\n' "${token}"
      return
    fi
  done

  die "Unable to discover a Supervisor API token from the running Home Assistant containers"
}


supervisor_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local url="http://$(supervisor_ip)${path}"
  local token

  require_tool curl
  require_tool docker
  require_tool jq

  token="$(supervisor_token)"

  if [[ -n "${body}" ]]; then
    curl -fsSL \
      -X "${method}" \
      -H "Authorization: Bearer ${token}" \
      -H "Content-Type: application/json" \
      -d "${body}" \
      "${url}"
    return
  fi

  curl -fsSL \
    -X "${method}" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    "${url}"
}


install_addon() {
  wait_for_ha

  echo "Refreshing the local add-on store entry..."
  ha store reload >/dev/null

  if addon_installed; then
    echo "Local add-on ${LOCAL_ADDON_SLUG} is already installed."
    return
  fi

  echo "Installing ${LOCAL_ADDON_SLUG} from the local repository..."
  ha store apps install "${LOCAL_ADDON_SLUG}"
}


configure_addon() {
  local options_file="${1:-${OPTIONS_FILE}}"
  local options_json
  local wrapped_options_json

  [[ -f "${options_file}" ]] || die "Missing dev options file: ${options_file}"

  wait_for_ha
  install_addon

  options_json="$(jq -c . "${options_file}")"
  wrapped_options_json="$(jq -cn --argjson options "${options_json}" '{options: $options}')"

  echo "Applying dev options from ${options_file}..."
  supervisor_api POST "/addons/${LOCAL_ADDON_SLUG}/options" "${wrapped_options_json}" >/dev/null

  echo "Disabling protection mode for ${LOCAL_ADDON_SLUG}..."
  supervisor_api POST "/addons/${LOCAL_ADDON_SLUG}/security" '{"protected":false}' >/dev/null
}


rebuild_addon() {
  wait_for_ha
  install_addon

  echo "Force rebuilding ${LOCAL_ADDON_SLUG}..."
  ha apps rebuild "${LOCAL_ADDON_SLUG}" --force
}


start_addon() {
  wait_for_ha
  install_addon

  echo "Starting ${LOCAL_ADDON_SLUG}..."
  ha apps start "${LOCAL_ADDON_SLUG}"
}


restart_addon() {
  wait_for_ha
  install_addon

  echo "Restarting ${LOCAL_ADDON_SLUG}..."
  ha apps restart "${LOCAL_ADDON_SLUG}"
}


show_status() {
  wait_for_ha
  install_addon

  ha apps info "${LOCAL_ADDON_SLUG}"
}


show_logs() {
  wait_for_ha
  install_addon

  ha apps logs "${LOCAL_ADDON_SLUG}" --follow
}


prepare_addon() {
  configure_addon
  rebuild_addon
  show_status
}


print_info() {
  local addon_dir_display="${ADDON_DIR#"${ROOT_DIR}/"}"
  local options_file_display="${OPTIONS_FILE#"${ROOT_DIR}/"}"

  cat <<EOF
Repository root: .
Add-on directory: ${addon_dir_display}
Add-on slug: ${ADDON_SLUG}
Local add-on slug: ${LOCAL_ADDON_SLUG}
Dev options file: ${options_file_display}

Suggested loop inside the Home Assistant devcontainer:
  1. Run the VS Code task: Start Home Assistant
  2. Run the VS Code task: Prepare Local Add-on
  3. Use Rebuild Local Add-on, Restart Local Add-on, and Follow Local Add-on Logs as needed
EOF
}


main() {
  local command="${1:-info}"
  shift || true

  init_context

  case "${command}" in
    info)
      print_info
      ;;
    slug)
      printf '%s\n' "${LOCAL_ADDON_SLUG}"
      ;;
    wait)
      wait_for_ha
      ;;
    install)
      install_addon
      ;;
    configure)
      configure_addon "${1:-${OPTIONS_FILE}}"
      ;;
    prepare|up)
      prepare_addon
      ;;
    rebuild)
      rebuild_addon
      ;;
    start)
      start_addon
      ;;
    restart)
      restart_addon
      ;;
    status)
      show_status
      ;;
    logs)
      show_logs
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage >&2
      die "Unknown command: ${command}"
      ;;
  esac
}


main "$@"

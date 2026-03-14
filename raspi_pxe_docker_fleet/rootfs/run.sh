#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

source /usr/local/lib/ha-pxe/common.sh

cleanup() {
  ha_pxe::shutdown
}

main() {
  ha_pxe::ensure_directories
  ha_pxe::reset_runtime_state
  ha_pxe::validate_config

  local server_ip
  server_ip="$(ha_pxe::resolve_server_ip)"
  export HA_PXE_SERVER_IP="${server_ip}"

  ha_pxe::log_info "Using ${server_ip} as the PXE/NFS endpoint"
  ha_pxe::write_dhcp_hints "${server_ip}"

  local clients_json
  clients_json="$(bashio::config 'clients')"
  if [[ -z "${clients_json}" || "${clients_json}" == "null" ]]; then
    clients_json='[]'
  fi

  while IFS= read -r client_json; do
    [[ -n "${client_json}" ]] || continue
    /usr/local/lib/ha-pxe/provision-client.sh "${client_json}" "${server_ip}"
  done < <(jq -c '.[]?' <<<"${clients_json}")

  ha_pxe::start_nfs_server
  ha_pxe::start_tftp_server

  ha_pxe::log_warning "DHCP or ProxyDHCP is not included. Your network must direct PXE clients to ${server_ip} for TFTP."

  while ((${#HA_PXE_BG_PIDS[@]} > 0)); do
    if wait -n "${HA_PXE_BG_PIDS[@]}"; then
      ha_pxe::log_error "A background service exited unexpectedly"
      return 1
    else
      local status=$?
      ha_pxe::log_error "A background service exited with status ${status}"
      return "${status}"
    fi
  done
}

trap cleanup EXIT INT TERM

main "$@"

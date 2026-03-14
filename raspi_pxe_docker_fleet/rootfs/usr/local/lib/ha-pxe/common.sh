#!/usr/bin/env bash
set -Eeuo pipefail

HA_PXE_ROOT="/data"
HA_PXE_CACHE_DIR="${HA_PXE_ROOT}/cache/images"
HA_PXE_EXPORTS_DIR="${HA_PXE_ROOT}/exports"
HA_PXE_RUNTIME_DIR="${HA_PXE_ROOT}/runtime"
HA_PXE_STATE_DIR="${HA_PXE_ROOT}/state/clients"
HA_PXE_TFTP_DIR="${HA_PXE_ROOT}/tftp"
HA_PXE_TMP_DIR="${HA_PXE_ROOT}/tmp"
HA_PXE_EXPORTS_FILE="${HA_PXE_RUNTIME_DIR}/exports"
HA_PXE_DHCP_HINTS_FILE="${HA_PXE_RUNTIME_DIR}/dhcp-example.txt"
HA_PXE_OS_PAGE="https://www.raspberrypi.com/software/operating-systems/"
HA_PXE_BG_PIDS=()

ha_pxe::log_info() {
  if declare -F bashio::log.info >/dev/null 2>&1; then
    bashio::log.info "$*"
  else
    echo "[INFO] $*" >&2
  fi
}

ha_pxe::log_warning() {
  if declare -F bashio::log.warning >/dev/null 2>&1; then
    bashio::log.warning "$*"
  else
    echo "[WARN] $*" >&2
  fi
}

ha_pxe::log_error() {
  if declare -F bashio::log.error >/dev/null 2>&1; then
    bashio::log.error "$*"
  else
    echo "[ERROR] $*" >&2
  fi
}

ha_pxe::ensure_directories() {
  mkdir -p \
    "${HA_PXE_CACHE_DIR}" \
    "${HA_PXE_EXPORTS_DIR}" \
    "${HA_PXE_RUNTIME_DIR}" \
    "${HA_PXE_STATE_DIR}" \
    "${HA_PXE_TFTP_DIR}" \
    "${HA_PXE_TMP_DIR}"
  : > "${HA_PXE_EXPORTS_FILE}"
}

ha_pxe::reset_runtime_state() {
  local mount_point

  while IFS= read -r mount_point; do
    [[ -n "${mount_point}" ]] || continue
    umount "${mount_point}" || true
  done < <(findmnt -rn -o TARGET | awk -v tftp_dir="${HA_PXE_TFTP_DIR}" '$0 ~ "^" tftp_dir "/" { print $0 }' | sort -r)

  find "${HA_PXE_TFTP_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  : > "${HA_PXE_EXPORTS_FILE}"
}

ha_pxe::validate_config() {
  local username password keys clients_json

  username="$(bashio::config 'default_username')"
  password="$(bashio::config 'default_password')"
  keys="$(bashio::config 'ssh_authorized_keys')"
  clients_json="$(bashio::config 'clients')"

  [[ "${password}" == "null" ]] && password=""
  [[ "${keys}" == "null" ]] && keys=""
  [[ "${clients_json}" == "null" ]] && clients_json="[]"

  if [[ -z "${password}" && -z "${keys}" ]]; then
    ha_pxe::log_error "Set either default_password or ssh_authorized_keys so the client user can be accessed"
    return 1
  fi

  if [[ -n "${clients_json}" && "${clients_json}" != "null" ]]; then
    if ! jq -e '([.[].serial | ascii_downcase | ltrimstr("0x")] | length) == ([.[].serial | ascii_downcase | ltrimstr("0x")] | unique | length)' <<<"${clients_json}" >/dev/null; then
      ha_pxe::log_error "Client serial numbers must be unique"
      return 1
    fi
  fi

  ha_pxe::log_info "Default client user is ${username}"
}

ha_pxe::resolve_server_ip() {
  local configured ip

  configured="$(bashio::config 'server_ip')"
  if [[ -n "${configured}" && "${configured}" != "null" ]]; then
    printf '%s\n' "${configured}"
    return 0
  fi

  ip="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
  if [[ -z "${ip}" ]]; then
    ip="$(hostname -I | awk '{print $1}')"
  fi

  if [[ -z "${ip}" ]]; then
    ha_pxe::log_error "Unable to auto-detect the server IP; set server_ip explicitly"
    return 1
  fi

  printf '%s\n' "${ip}"
}

ha_pxe::normalize_serial() {
  local serial="${1,,}"
  serial="${serial#0x}"
  if [[ ! "${serial}" =~ ^[0-9a-f]+$ ]]; then
    ha_pxe::log_error "Invalid serial: ${1}"
    return 1
  fi
  printf '%s\n' "${serial}"
}

ha_pxe::validate_model() {
  case "${1}" in
    pi0|pi1|pi2|pi3|pi4|pi5|400|500|cm3|cm4|cm5|zero2w)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ha_pxe::warn_if_model_needs_manual_attention() {
  case "${1}" in
    pi0|pi1)
      ha_pxe::log_warning "Model ${1} does not have a typical onboard PXE path; image selection will work, but network boot may not"
      ;;
    zero2w)
      ha_pxe::log_warning "Model zero2w does not support standard wired network boot; image selection will work, but PXE may not"
      ;;
  esac
}

ha_pxe::image_arch_for_model() {
  local model="${1}"
  local override="${2:-auto}"

  if [[ "${override}" != "auto" && -n "${override}" ]]; then
    printf '%s\n' "${override}"
    return 0
  fi

  case "${model}" in
    pi0|pi1|pi2)
      printf 'armhf\n'
      ;;
    *)
      printf 'arm64\n'
      ;;
  esac
}

ha_pxe::latest_image_url() {
  local arch="${1}"
  local page pattern url

  page="$(curl -fsSL "${HA_PXE_OS_PAGE}")"
  if [[ "${arch}" == "armhf" ]]; then
    pattern='https://downloads\.raspberrypi\.com/raspios_lite_armhf/images/[^"[:space:]]+\.img\.xz'
  else
    pattern='https://downloads\.raspberrypi\.com/raspios_lite_arm64/images/[^"[:space:]]+\.img\.xz'
  fi

  url="$(grep -oE "${pattern}" <<<"${page}" | head -n1)"
  if [[ -z "${url}" ]]; then
    ha_pxe::log_error "Unable to discover the latest Raspberry Pi OS Lite ${arch} image URL"
    return 1
  fi

  printf '%s\n' "${url}"
}

ha_pxe::download_image() {
  local url="${1}"
  local archive_path image_path tmp_path

  archive_path="${HA_PXE_CACHE_DIR}/${url##*/}"
  image_path="${archive_path%.xz}"
  tmp_path="${archive_path}.download"

  if [[ ! -s "${archive_path}" ]]; then
    ha_pxe::log_info "Downloading ${url##*/}"
    curl -fL "${url}" -o "${tmp_path}"
    mv "${tmp_path}" "${archive_path}"
  else
    ha_pxe::log_info "Reusing cached image archive ${archive_path##*/}"
  fi

  if [[ ! -s "${image_path}" || "${archive_path}" -nt "${image_path}" ]]; then
    ha_pxe::log_info "Decompressing ${archive_path##*/}"
    xz -dc "${archive_path}" > "${image_path}.tmp"
    mv "${image_path}.tmp" "${image_path}"
  fi

  printf '%s\n' "${image_path}"
}

ha_pxe::partition_offset_bytes() {
  local image_path="${1}"
  local partition_number="${2}"

  parted -sm "${image_path}" unit B print | awk -F: -v partition_number="${partition_number}" '
    $1 == partition_number {
      gsub(/B/, "", $2)
      print $2
      exit
    }
  '
}

ha_pxe::clear_directory() {
  local dir="${1}"
  mkdir -p "${dir}"
  find "${dir}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

ha_pxe::populate_from_image() {
  local image_path="${1}"
  local boot_dir="${2}"
  local root_dir="${3}"
  local boot_offset root_offset mount_boot mount_root
  local mounted_boot=false mounted_root=false

  cleanup_mounts() {
    if [[ "${mounted_root}" == "true" ]]; then
      umount "${mount_root}" || true
    fi
    if [[ "${mounted_boot}" == "true" ]]; then
      umount "${mount_boot}" || true
    fi
    rmdir "${mount_boot}" "${mount_root}" 2>/dev/null || true
  }

  boot_offset="$(ha_pxe::partition_offset_bytes "${image_path}" 1)"
  root_offset="$(ha_pxe::partition_offset_bytes "${image_path}" 2)"

  if [[ -z "${boot_offset}" || -z "${root_offset}" ]]; then
    ha_pxe::log_error "Unable to determine image partition offsets for ${image_path}"
    return 1
  fi

  mount_boot="$(mktemp -d "${HA_PXE_TMP_DIR}/boot.XXXXXX")"
  mount_root="$(mktemp -d "${HA_PXE_TMP_DIR}/root.XXXXXX")"
  trap cleanup_mounts RETURN

  mount -o loop,ro,offset="${boot_offset}" -t vfat "${image_path}" "${mount_boot}"
  mounted_boot=true
  mount -o loop,ro,offset="${root_offset}" -t ext4 "${image_path}" "${mount_root}"
  mounted_root=true

  ha_pxe::clear_directory "${boot_dir}"
  ha_pxe::clear_directory "${root_dir}"

  rsync -a --delete "${mount_boot}/" "${boot_dir}/"
  rsync -aHAX --numeric-ids --delete "${mount_root}/" "${root_dir}/"

  sync
  trap - RETURN
  cleanup_mounts
}

ha_pxe::append_exports() {
  local boot_dir="${1}"
  local root_dir="${2}"

  cat >> "${HA_PXE_EXPORTS_FILE}" <<EOF
${root_dir} *(rw,sync,no_subtree_check,no_root_squash,insecure)
${boot_dir} *(rw,sync,no_subtree_check,no_root_squash,insecure)
EOF
}

ha_pxe::bind_tftp_tree() {
  local boot_dir="${1}"
  local serial="${2}"
  local short_serial="${3}"
  local full_target="${HA_PXE_TFTP_DIR}/${serial}"
  local short_target="${HA_PXE_TFTP_DIR}/${short_serial}"

  mkdir -p "${full_target}" "${short_target}"
  mount --bind "${boot_dir}" "${full_target}"
  if [[ "${short_serial}" != "${serial}" ]]; then
    mount --bind "${boot_dir}" "${short_target}"
  fi
}

ha_pxe::write_client_state() {
  local state_file="${1}"
  local model="${2}"
  local arch="${3}"
  local image_url="${4}"

  jq -n \
    --arg model "${model}" \
    --arg arch "${arch}" \
    --arg image_url "${image_url}" \
    '{model: $model, arch: $arch, image_url: $image_url}' > "${state_file}"
}

ha_pxe::write_dhcp_hints() {
  local server_ip="${1}"

  cat > "${HA_PXE_DHCP_HINTS_FILE}" <<EOF
Point Raspberry Pi PXE clients at ${server_ip}

Required DHCP concepts:
- next-server / option 66: ${server_ip}
- boot file / option 67: bootcode.bin or the matching Raspberry Pi firmware entrypoint for your board

This add-on does not run DHCP or ProxyDHCP.
EOF
}

ha_pxe::start_nfs_server() {
  mkdir -p /var/lib/nfs/rpc_pipefs /proc/fs/nfsd
  cp "${HA_PXE_EXPORTS_FILE}" /etc/exports

  mountpoint -q /var/lib/nfs/rpc_pipefs || mount -t rpc_pipefs sunrpc /var/lib/nfs/rpc_pipefs
  mountpoint -q /proc/fs/nfsd || mount -t nfsd nfsd /proc/fs/nfsd

  rpcbind -f -w &
  HA_PXE_BG_PIDS+=("$!")
  sleep 1

  exportfs -ra
  rpc.mountd -F --manage-gids &
  HA_PXE_BG_PIDS+=("$!")

  rpc.nfsd 8
  ha_pxe::log_info "NFS exports are active"
}

ha_pxe::start_tftp_server() {
  in.tftpd --foreground --listen --address 0.0.0.0:69 --secure "${HA_PXE_TFTP_DIR}" &
  HA_PXE_BG_PIDS+=("$!")
  ha_pxe::log_info "TFTP server is active on UDP 69"
}

ha_pxe::shutdown() {
  local pid mount_point

  for pid in "${HA_PXE_BG_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
  done

  while IFS= read -r mount_point; do
    [[ -n "${mount_point}" ]] || continue
    umount "${mount_point}" || true
  done < <(findmnt -rn -o TARGET | awk -v tftp_dir="${HA_PXE_TFTP_DIR}" '$0 ~ "^" tftp_dir "/" { print $0 }' | sort -r)
}

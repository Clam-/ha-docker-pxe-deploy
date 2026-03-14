#!/usr/bin/env bash
set -Eeuo pipefail

source /usr/lib/bashio/bashio.sh
source /usr/local/lib/ha-pxe/common.sh

rewrite_cmdline() {
  local boot_dir="${1}"
  local server_ip="${2}"
  local root_export="${3}"
  local existing token
  local cleaned_prefix=""
  local -a cleaned_tokens=()

  existing="$(tr '\n' ' ' < "${boot_dir}/cmdline.txt" | tr -s '[:space:]' ' ')"

  for token in ${existing}; do
    case "${token}" in
      root=*|rootfstype=*|rootwait|nfsroot=*|ip=*|init=*|rw|ro|resize|systemd.run=*|systemd.run_success_action=*|systemd.unit=*)
        continue
        ;;
    esac

    if [[ " ${cleaned_tokens[*]} " == *" ${token} "* ]]; then
      continue
    fi

    cleaned_tokens+=("${token}")
  done

  ha_pxe::log_debug "Original cmdline for ${boot_dir}: ${existing}"
  if ((${#cleaned_tokens[@]} > 0)); then
    cleaned_prefix="${cleaned_tokens[*]} "
  fi

  cat > "${boot_dir}/cmdline.txt" <<EOF
${cleaned_prefix}root=/dev/nfs nfsroot=${server_ip}:${root_export},vers=3,tcp,nolock rw ip=dhcp rootwait
EOF
  ha_pxe::log_debug "Rewritten cmdline for ${boot_dir}: $(cat "${boot_dir}/cmdline.txt")"
}

rewrite_fstab() {
  local root_dir="${1}"
  local server_ip="${2}"
  local boot_export="${3}"
  local temp_file

  temp_file="$(mktemp "${HA_PXE_TMP_DIR}/fstab.XXXXXX")"
  awk '
    /^[[:space:]]*#/ || NF == 0 { print; next }
    $2 == "/" { next }
    $2 == "/boot" { next }
    $2 == "/boot/firmware" { next }
    { print }
  ' "${root_dir}/etc/fstab" > "${temp_file}"

  cat >> "${temp_file}" <<EOF
${server_ip}:${boot_export} /boot/firmware nfs defaults,vers=3,tcp,nolock,_netdev 0 0
EOF
  mv "${temp_file}" "${root_dir}/etc/fstab"
}

write_bootstrap_files() {
  local root_dir="${1}"
  local serial="${2}"
  local hostname="${3}"
  local containers_raw="${4}"
  local username password keys groups group authorized_keys_path

  username="$(ha_pxe::config_string '.default_username')"
  password="$(ha_pxe::config_string '.default_password')"
  keys="$(ha_pxe::config_string '.ssh_authorized_keys')"

  mkdir -p "${root_dir}/etc/ha-pxe"
  mkdir -p "${root_dir}/usr/local/sbin"
  mkdir -p "${root_dir}/etc/systemd/system/multi-user.target.wants"
  mkdir -p "${root_dir}/etc/systemd/system/timers.target.wants"
  mkdir -p "${root_dir}/var/lib/ha-pxe"

  groups=""
  for group in sudo adm dialout cdrom audio video plugdev users input netdev gpio i2c spi render docker; do
    groups+="${group},"
  done
  groups="${groups%,}"

  install -m 0644 /usr/local/lib/ha-pxe/templates/ha-pxe-firstboot.service "${root_dir}/etc/systemd/system/ha-pxe-firstboot.service"
  install -m 0644 /usr/local/lib/ha-pxe/templates/ha-pxe-container-sync.service "${root_dir}/etc/systemd/system/ha-pxe-container-sync.service"
  install -m 0644 /usr/local/lib/ha-pxe/templates/ha-pxe-container-sync.timer "${root_dir}/etc/systemd/system/ha-pxe-container-sync.timer"
  install -m 0755 /usr/local/lib/ha-pxe/templates/ha-pxe-firstboot.sh "${root_dir}/usr/local/sbin/ha-pxe-firstboot"
  install -m 0755 /usr/local/lib/ha-pxe/templates/ha-pxe-container-sync.sh "${root_dir}/usr/local/sbin/ha-pxe-container-sync"

  ln -snf ../ha-pxe-firstboot.service "${root_dir}/etc/systemd/system/multi-user.target.wants/ha-pxe-firstboot.service"
  ln -snf ../ha-pxe-container-sync.timer "${root_dir}/etc/systemd/system/timers.target.wants/ha-pxe-container-sync.timer"

  cat > "${root_dir}/etc/ha-pxe/bootstrap.env" <<EOF
PXE_USERNAME=$(printf '%q' "${username}")
PXE_PASSWORD_HASH=$(printf '%q' "${password:+$(openssl passwd -6 "${password}")}")
PXE_HOSTNAME=$(printf '%q' "${hostname}")
PXE_SERIAL=$(printf '%q' "${serial}")
PXE_EXTRA_GROUPS=$(printf '%q' "${groups}")
EOF

  authorized_keys_path="${root_dir}/etc/ha-pxe/authorized_keys"
  if [[ -n "${keys}" ]]; then
    printf '%s\n' "${keys}" > "${authorized_keys_path}"
  else
    : > "${authorized_keys_path}"
  fi

  printf '%s\n' "${containers_raw}" | sed -e 's/\r$//' -e '/^[[:space:]]*$/d' > "${root_dir}/etc/ha-pxe/containers.txt"

  printf '%s\n' "${hostname}" > "${root_dir}/etc/hostname"

  if [[ -f "${root_dir}/etc/hosts" ]]; then
    if grep -q '^127\.0\.1\.1[[:space:]]' "${root_dir}/etc/hosts"; then
      sed -i -E "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t${hostname}/" "${root_dir}/etc/hosts"
    else
      printf '127.0.1.1\t%s\n' "${hostname}" >> "${root_dir}/etc/hosts"
    fi
  fi
}

main() {
  local client_json="${1}"
  local server_ip="${2}"
  local serial model hostname arch_override arch rebuild containers_raw
  local short_serial boot_dir root_dir state_file image_url image_path
  local existing_arch existing_model

  serial="$(ha_pxe::normalize_serial "$(jq -r '.serial' <<<"${client_json}")")"
  short_serial="${serial: -8}"
  model="$(jq -r '.model' <<<"${client_json}")"
  hostname="$(jq -r '.hostname' <<<"${client_json}")"
  arch_override="$(jq -r '.image_arch // "auto"' <<<"${client_json}")"
  rebuild="$(jq -r '.rebuild // false' <<<"${client_json}")"
  containers_raw="$(jq -r '.containers // ""' <<<"${client_json}")"

  if ! ha_pxe::validate_model "${model}"; then
    ha_pxe::log_error "Unsupported model '${model}' for client ${serial}"
    return 1
  fi

  ha_pxe::warn_if_model_needs_manual_attention "${model}"
  arch="$(ha_pxe::image_arch_for_model "${model}" "${arch_override}")"
  ha_pxe::log_info "Preparing client ${hostname} (${serial}) for model ${model} with image arch ${arch}"

  boot_dir="${HA_PXE_EXPORTS_DIR}/${serial}/boot"
  root_dir="${HA_PXE_EXPORTS_DIR}/${serial}/root"
  state_file="${HA_PXE_STATE_DIR}/${serial}.json"

  mkdir -p "${boot_dir}" "${root_dir}"

  if [[ "${rebuild}" == "true" ]]; then
    ha_pxe::log_warning "Rebuilding exports for ${serial}"
    ha_pxe::clear_directory "${boot_dir}"
    ha_pxe::clear_directory "${root_dir}"
    rm -f "${state_file}"
  fi

  if [[ -f "${state_file}" ]]; then
    existing_model="$(jq -r '.model // empty' "${state_file}")"
    existing_arch="$(jq -r '.arch // empty' "${state_file}")"
    if [[ -n "${existing_model}" && "${existing_model}" != "${model}" ]]; then
      ha_pxe::log_warning "Client ${serial} model changed from ${existing_model} to ${model}; set rebuild: true to refresh the exported rootfs"
    fi
    if [[ -n "${existing_arch}" && "${existing_arch}" != "${arch}" ]]; then
      ha_pxe::log_error "Client ${serial} architecture changed from ${existing_arch} to ${arch}; set rebuild: true before restarting the add-on"
      return 1
    fi
  fi

  if [[ ! -f "${root_dir}/etc/os-release" || ! -f "${boot_dir}/cmdline.txt" ]]; then
    image_url="$(ha_pxe::latest_image_url "${arch}")"
    ha_pxe::log_info "Selected Raspberry Pi OS image ${image_url}"
    image_path="$(ha_pxe::download_image "${image_url}")"
    ha_pxe::log_info "Populating exports for ${serial} from ${image_path}"
    ha_pxe::populate_from_image "${image_path}" "${boot_dir}" "${root_dir}"
    ha_pxe::write_client_state "${state_file}" "${model}" "${arch}" "${image_url}"
  fi

  ha_pxe::log_info "Writing PXE boot configuration for ${serial}"
  rewrite_cmdline "${boot_dir}" "${server_ip}" "${root_dir}"
  rewrite_fstab "${root_dir}" "${server_ip}" "${boot_dir}"
  ha_pxe::log_info "Injecting first-boot bootstrap for ${serial}"
  write_bootstrap_files "${root_dir}" "${serial}" "${hostname}" "${containers_raw}"
  ha_pxe::log_info "Registering NFS exports for ${serial}"
  ha_pxe::append_exports "${boot_dir}" "${root_dir}"
  ha_pxe::log_info "Binding TFTP trees for ${serial} and ${short_serial}"
  ha_pxe::bind_tftp_tree "${boot_dir}" "${serial}" "${short_serial}"

  ha_pxe::log_info "Prepared client ${hostname} (${serial}) using ${arch}"
}

main "$@"

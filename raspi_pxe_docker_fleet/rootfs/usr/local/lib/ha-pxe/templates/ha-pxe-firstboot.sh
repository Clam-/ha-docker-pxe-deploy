#!/bin/bash
set -Eeuo pipefail

source /etc/ha-pxe/bootstrap.env

MARKER_FILE="/var/lib/ha-pxe/firstboot.done"

ensure_group_memberships() {
  local group
  IFS=',' read -ra groups <<<"${PXE_EXTRA_GROUPS:-}"
  for group in "${groups[@]}"; do
    [[ -n "${group}" ]] || continue
    if getent group "${group}" >/dev/null 2>&1; then
      usermod -aG "${group}" "${PXE_USERNAME}" || true
    fi
  done
}

configure_ssh_keys() {
  if [[ ! -s /etc/ha-pxe/authorized_keys ]]; then
    return 0
  fi

  install -d -m 700 -o "${PXE_USERNAME}" -g "${PXE_USERNAME}" "/home/${PXE_USERNAME}/.ssh"
  install -m 600 -o "${PXE_USERNAME}" -g "${PXE_USERNAME}" /etc/ha-pxe/authorized_keys "/home/${PXE_USERNAME}/.ssh/authorized_keys"
}

main() {
  mkdir -p /var/lib/ha-pxe

  if [[ -f "${MARKER_FILE}" ]]; then
    exit 0
  fi

  hostnamectl set-hostname "${PXE_HOSTNAME}" || true

  if [[ ! -f /etc/hostname || "$(cat /etc/hostname)" != "${PXE_HOSTNAME}" ]]; then
    printf '%s\n' "${PXE_HOSTNAME}" > /etc/hostname
  fi

  if [[ -f /etc/hosts ]]; then
    if grep -q '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
      sed -i -E "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t${PXE_HOSTNAME}/" /etc/hosts
    else
      printf '127.0.1.1\t%s\n' "${PXE_HOSTNAME}" >> /etc/hosts
    fi
  fi

  if ! id "${PXE_USERNAME}" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "${PXE_USERNAME}"
  fi

  if [[ -n "${PXE_PASSWORD_HASH:-}" ]]; then
    usermod -p "${PXE_PASSWORD_HASH}" "${PXE_USERNAME}"
  else
    passwd -l "${PXE_USERNAME}" || true
  fi

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates docker.io jq

  ensure_group_memberships
  configure_ssh_keys

  systemctl daemon-reload
  systemctl enable docker.service
  systemctl enable containerd.service || true
  systemctl enable ssh.service || true
  systemctl enable ha-pxe-container-sync.timer
  systemctl start docker.service
  systemctl start ssh.service || true
  systemctl start ha-pxe-container-sync.service || true
  systemctl start ha-pxe-container-sync.timer

  touch "${MARKER_FILE}"
}

main "$@"

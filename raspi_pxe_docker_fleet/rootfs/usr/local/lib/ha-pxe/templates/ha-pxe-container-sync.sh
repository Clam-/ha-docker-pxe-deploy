#!/bin/bash
set -Eeuo pipefail

source /etc/ha-pxe/bootstrap.env

image_to_name() {
  local image="${1}"
  local base hash

  base="${image##*/}"
  base="${base%%@*}"
  base="${base%%:*}"
  base="$(tr '[:upper:]' '[:lower:]' <<<"${base}" | sed 's/[^a-z0-9_.-]/-/g')"
  hash="$(printf '%s' "${image}" | sha256sum | awk '{print $1}' | cut -c1-8)"
  printf 'ha-pxe-%s-%s\n' "${base}" "${hash}"
}

ensure_docker_running() {
  systemctl start docker.service
}

main() {
  local desired_file="/etc/ha-pxe/containers.txt"
  local image name desired_id current_id existing
  declare -A desired_names=()

  if ! command -v docker >/dev/null 2>&1; then
    exit 0
  fi

  ensure_docker_running

  while IFS= read -r image; do
    [[ -n "${image}" ]] || continue
    name="$(image_to_name "${image}")"
    desired_names["${name}"]="${image}"

    docker pull "${image}"
    desired_id="$(docker image inspect --format '{{.Id}}' "${image}")"
    current_id="$(docker inspect --format '{{.Image}}' "${name}" 2>/dev/null || true)"

    if [[ -z "${current_id}" ]]; then
      docker run -d --restart unless-stopped \
        --name "${name}" \
        --label io.ha_pxe.managed=true \
        --label io.ha_pxe.client_serial="${PXE_SERIAL}" \
        "${image}"
      continue
    fi

    if [[ "${current_id}" != "${desired_id}" ]]; then
      docker rm -f "${name}"
      docker run -d --restart unless-stopped \
        --name "${name}" \
        --label io.ha_pxe.managed=true \
        --label io.ha_pxe.client_serial="${PXE_SERIAL}" \
        "${image}"
      continue
    fi

    docker start "${name}" >/dev/null 2>&1 || true
  done < <(sed -e 's/\r$//' -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "${desired_file}")

  while IFS= read -r existing; do
    [[ -n "${existing}" ]] || continue
    if [[ -z "${desired_names[${existing}]+set}" ]]; then
      docker rm -f "${existing}"
    fi
  done < <(docker ps -a --filter label=io.ha_pxe.managed=true --format '{{.Names}}')
}

main "$@"

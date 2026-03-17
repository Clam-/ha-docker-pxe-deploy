#!/usr/bin/env bash

set -euo pipefail

source /etc/supervisor_scripts/common

readonly REGISTRY_PROBE_IMAGES=(
  docker:29.3.0-cli
  ghcr.io/home-assistant/aarch64-base:latest
)


run_supervisor_container() {
  validate_devcontainer "apps"

  docker run --rm --privileged \
    --name hassio_supervisor \
    --privileged \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    -v /run/docker.sock:/run/docker.sock:rw \
    -v /run/dbus:/run/dbus:ro \
    -v /run/udev:/run/udev:ro \
    -v /mnt/supervisor:/data:rw \
    -v /etc/machine-id:/etc/machine-id:ro \
    -e SUPERVISOR_SHARE="/mnt/supervisor" \
    -e SUPERVISOR_NAME=hassio_supervisor \
    -e SUPERVISOR_DEV=1 \
    -e SUPERVISOR_MACHINE="qemu${QEMU_ARCH}" \
    -e SUPERVISOR_SYSTEMD_JOURNAL_GATEWAYD_URL="http://172.30.32.1:19531/" \
    "${SUPERVISOR_IMAGE}:${SUPERVISOR_VERSION}"
}


ensure_registry_access() {
  local image
  local attempt

  for image in "${REGISTRY_PROBE_IMAGES[@]}"; do
    for attempt in 1 2 3; do
      echo "Checking nested Docker registry access with ${image} (attempt ${attempt}/3)..."
      if docker pull "${image}" >/dev/null 2>&1; then
        echo "Nested Docker can reach ${image}."
        break
      fi

      if [[ "${attempt}" -eq 3 ]]; then
        echo "Nested Docker could not pull ${image} after ${attempt} attempts." >&2
        return 1
      fi

      echo "Nested Docker pull failed, restarting the daemon and retrying..."
      stop_docker || true
      start_docker
      sleep 2
    done
  done
}


main() {
  start_systemd_journald
  start_docker
  trap "stop_docker" ERR

  ensure_registry_access

  if [[ "$(docker container inspect -f '{{.State.Status}}' hassio_supervisor 2>/dev/null || true)" == "running" ]]; then
    echo "Restarting Supervisor"
    docker rm -f hassio_supervisor
    init_dbus
    init_udev
    init_os_agent
    cleanup_lastboot
    run_supervisor_container
    stop_docker
    return
  fi

  echo "Starting Supervisor"
  docker system prune -f
  cleanup_lastboot
  cleanup_docker
  init_dbus
  init_udev
  init_os_agent
  run_supervisor_container
  stop_docker
}


main "$@"

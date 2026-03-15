#!/bin/bash
set -Eeuo pipefail

source /etc/ha-pxe/bootstrap.env

STATE_ROOT="/var/lib/ha-pxe/containers"
DESIRED_FILE="/etc/ha-pxe/containers.json"

HA_PXE_BUILD_CONTEXT=""
HA_PXE_BUILD_DOCKERFILE=""
HA_PXE_BUILD_FINGERPRINT=""
HA_PXE_MATERIALIZED_FILE_MOUNTS=()

log_info() {
  printf '[ha-pxe-container-sync] %s\n' "$*" >&2
}

log_error() {
  printf '[ha-pxe-container-sync] %s\n' "$*" >&2
}

slug() {
  local value="${1,,}"

  value="$(sed -E 's/[^a-z0-9_.-]+/-/g; s/^-+//; s/-+$//' <<<"${value}")"
  if [[ -z "${value}" ]]; then
    value="container"
  fi

  printf '%s\n' "${value}"
}

sha256_text() {
  printf '%s' "${1}" | sha256sum | awk '{print $1}'
}

spec_key() {
  local spec="${1}"
  local name identity hash

  name="$(jq -r '.name' <<<"${spec}")"
  identity="$(jq -cS '{name, source}' <<<"${spec}")"
  hash="$(sha256_text "${identity}" | cut -c1-8)"
  printf '%s-%s\n' "$(slug "${name}")" "${hash}"
}

container_name_for_spec() {
  printf 'ha-pxe-%s\n' "$(spec_key "${1}")"
}

container_dir_for_spec() {
  printf '%s/%s\n' "${STATE_ROOT}" "$(spec_key "${1}")"
}

spec_hash() {
  jq -cS '.' <<<"${1}" | sha256sum | awk '{print $1}'
}

ensure_docker_running() {
  systemctl start docker.service
}

sort_container_specs_json() {
  local specs_json="${1:-[]}"
  local sorted

  if ! sorted="$(
    jq -cS '
      def topo_sort:
        (map({key: .name, value: .}) | from_entries) as $specs
        | def visit($name; $state):
            if $state.permanent[$name] then
              $state
            elif $state.temporary[$name] then
              error("Container dependency cycle detected involving \($name)")
            else
              ($specs[$name] // error("Container depends_on references an unknown container: \($name)")) as $spec
              | reduce $spec.depends_on[] as $dep (
                  ($state | .temporary[$name] = true);
                  if $dep == $name then
                    error("Container \($name) cannot depend on itself")
                  elif ($specs[$dep] | type) == "null" then
                    error("Container \($name) depends on undefined container \($dep)")
                  else
                    visit($dep; .)
                  end
                )
              | .temporary |= del(.[$name])
              | .permanent[$name] = true
              | .ordered += [$spec]
            end;

        reduce .[].name as $name (
          {temporary: {}, permanent: {}, ordered: []};
          visit($name; .)
        )
        | .ordered;

      topo_sort
    ' <<<"${specs_json}" 2>&1
  )"; then
    sorted="$(sed -E 's/^jq: error \(at <stdin>:[0-9]+\): //' <<<"${sorted}")"
    log_error "${sorted}"
    return 1
  fi

  printf '%s\n' "${sorted}"
}

resolve_relative_path() {
  local base_dir="${1}"
  local relative_path="${2}"
  local kind="${3}"
  local resolved

  if [[ -z "${relative_path}" || "${relative_path}" == "." ]]; then
    printf '%s\n' "${base_dir}"
    return 0
  fi

  if [[ "${relative_path}" == /* ]]; then
    log_error "${kind} must be relative: ${relative_path}"
    return 1
  fi

  if [[ "${relative_path}" =~ (^|/)\.\.(/|$) ]]; then
    log_error "${kind} must stay inside the managed source tree: ${relative_path}"
    return 1
  fi

  resolved="${base_dir}/${relative_path}"
  printf '%s\n' "${resolved}"
}

prepare_git_build() {
  local spec="${1}"
  local state_dir="${2}"
  local repo_dir="${state_dir}/source/repo"
  local source_url source_ref context_rel dockerfile_rel revision target

  source_url="$(jq -r '.source.url' <<<"${spec}")"
  source_ref="$(jq -r '.source.ref' <<<"${spec}")"
  context_rel="$(jq -r '.source.context' <<<"${spec}")"
  dockerfile_rel="$(jq -r '.source.dockerfile' <<<"${spec}")"

  mkdir -p "${state_dir}/source"

  if [[ ! -d "${repo_dir}/.git" ]]; then
    rm -rf "${repo_dir}"
    git clone --no-checkout "${source_url}" "${repo_dir}"
  fi

  git -C "${repo_dir}" remote set-url origin "${source_url}"
  git -C "${repo_dir}" fetch --tags --prune origin
  git -C "${repo_dir}" fetch origin "${source_ref}" || true

  target="${source_ref}"
  if git -C "${repo_dir}" show-ref --verify --quiet "refs/remotes/origin/${source_ref}"; then
    target="origin/${source_ref}"
  elif git -C "${repo_dir}" show-ref --verify --quiet "refs/tags/${source_ref}"; then
    target="${source_ref}"
  fi

  git -C "${repo_dir}" checkout --force "${target}"
  git -C "${repo_dir}" reset --hard "${target}"
  git -C "${repo_dir}" clean -fdx
  revision="$(git -C "${repo_dir}" rev-parse HEAD)"

  HA_PXE_BUILD_CONTEXT="$(resolve_relative_path "${repo_dir}" "${context_rel}" "git build context")"
  HA_PXE_BUILD_DOCKERFILE="$(resolve_relative_path "${repo_dir}" "${dockerfile_rel}" "git Dockerfile path")"

  if [[ ! -d "${HA_PXE_BUILD_CONTEXT}" ]]; then
    log_error "Git build context does not exist: ${HA_PXE_BUILD_CONTEXT}"
    return 1
  fi

  if [[ ! -f "${HA_PXE_BUILD_DOCKERFILE}" ]]; then
    log_error "Git Dockerfile does not exist: ${HA_PXE_BUILD_DOCKERFILE}"
    return 1
  fi

  HA_PXE_BUILD_FINGERPRINT="$(
    printf '%s\n%s\n%s\n%s\n%s\n' \
      "${revision}" \
      "${context_rel}" \
      "${dockerfile_rel}" \
      "$(jq -cS '.source.build_args' <<<"${spec}")" \
      "$(jq -r '.image' <<<"${spec}")" \
      | sha256sum | awk '{print $1}'
  )"
}

prepare_remote_dockerfile_build() {
  local spec="${1}"
  local state_dir="${2}"
  local source_dir="${state_dir}/source/remote-dockerfile"
  local dockerfile_rel dockerfile_path dockerfile_hash source_url

  source_url="$(jq -r '.source.url' <<<"${spec}")"
  dockerfile_rel="$(jq -r '.source.dockerfile' <<<"${spec}")"

  HA_PXE_BUILD_CONTEXT="${source_dir}/context"
  HA_PXE_BUILD_DOCKERFILE="$(resolve_relative_path "${source_dir}" "${dockerfile_rel}" "remote Dockerfile path")"

  mkdir -p "${HA_PXE_BUILD_CONTEXT}" "$(dirname "${HA_PXE_BUILD_DOCKERFILE}")"
  curl -fsSL "${source_url}" -o "${HA_PXE_BUILD_DOCKERFILE}.tmp"
  mv "${HA_PXE_BUILD_DOCKERFILE}.tmp" "${HA_PXE_BUILD_DOCKERFILE}"

  dockerfile_hash="$(sha256sum "${HA_PXE_BUILD_DOCKERFILE}" | awk '{print $1}')"
  HA_PXE_BUILD_FINGERPRINT="$(
    printf '%s\n%s\n%s\n' \
      "${source_url}" \
      "${dockerfile_hash}" \
      "$(jq -cS '.source.build_args' <<<"${spec}")" \
      | sha256sum | awk '{print $1}'
  )"
}

build_image_if_needed() {
  local spec="${1}"
  local key="${2}"
  local image_name existing_fingerprint
  local -a build_cmd=()

  image_name="$(jq -r '.image' <<<"${spec}")"
  existing_fingerprint="$(docker image inspect --format '{{ index .Config.Labels "io.ha_pxe.build_fingerprint" }}' "${image_name}" 2>/dev/null || true)"

  if [[ "${existing_fingerprint}" != "${HA_PXE_BUILD_FINGERPRINT}" ]]; then
    build_cmd=(
      docker build
      -t "${image_name}"
      --label "io.ha_pxe.managed=true"
      --label "io.ha_pxe.client_serial=${PXE_SERIAL}"
      --label "io.ha_pxe.container_key=${key}"
      --label "io.ha_pxe.build_fingerprint=${HA_PXE_BUILD_FINGERPRINT}"
      -f "${HA_PXE_BUILD_DOCKERFILE}"
    )

    while IFS= read -r build_arg; do
      [[ -n "${build_arg}" ]] || continue
      build_cmd+=(--build-arg "${build_arg}")
    done < <(jq -r '.source.build_args | to_entries[]? | "\(.key)=\(.value)"' <<<"${spec}")

    build_cmd+=("${HA_PXE_BUILD_CONTEXT}")

    log_info "Building ${image_name}"
    "${build_cmd[@]}" >&2
  fi

  docker image inspect --format '{{.Id}}' "${image_name}"
}

ensure_desired_image() {
  local spec="${1}"
  local key="${2}"
  local state_dir="${3}"
  local source_type image_name

  source_type="$(jq -r '.source.type' <<<"${spec}")"
  image_name="$(jq -r '.image' <<<"${spec}")"

  case "${source_type}" in
    image)
      log_info "Pulling ${image_name}"
      docker pull "${image_name}" >/dev/null
      docker image inspect --format '{{.Id}}' "${image_name}"
      ;;
    git)
      prepare_git_build "${spec}" "${state_dir}"
      build_image_if_needed "${spec}" "${key}"
      ;;
    dockerfile_url)
      prepare_remote_dockerfile_build "${spec}" "${state_dir}"
      build_image_if_needed "${spec}" "${key}"
      ;;
    *)
      log_error "Unsupported source type: ${source_type}"
      return 1
      ;;
  esac
}

materialize_files() {
  local spec="${1}"
  local state_dir="${2}"
  local files_dir="${state_dir}/files"
  local file_json container_path host_path format mode content volume

  HA_PXE_MATERIALIZED_FILE_MOUNTS=()

  rm -rf "${files_dir}"
  mkdir -p "${files_dir}"

  while IFS= read -r file_json; do
    [[ -n "${file_json}" ]] || continue
    container_path="$(jq -r '.container_path' <<<"${file_json}")"
    host_path="${files_dir}/${container_path#/}"
    format="$(jq -r '.format' <<<"${file_json}")"
    mode="$(jq -r '.mode' <<<"${file_json}")"

    mkdir -p "$(dirname "${host_path}")"

    case "${format}" in
      json)
        jq -S '.content' <<<"${file_json}" > "${host_path}.tmp"
        ;;
      text)
        content="$(jq -r 'if (.content | type) == "string" then .content else (.content | tostring) end' <<<"${file_json}")"
        printf '%s' "${content}" > "${host_path}.tmp"
        ;;
      *)
        log_error "Unsupported file format '${format}' for ${container_path}"
        return 1
        ;;
    esac

    mv "${host_path}.tmp" "${host_path}"
    chmod "${mode}" "${host_path}"

    volume="${host_path}:${container_path}"
    if [[ "$(jq -r '.read_only' <<<"${file_json}")" == "true" ]]; then
      volume="${volume}:ro"
    fi

    HA_PXE_MATERIALIZED_FILE_MOUNTS+=("${volume}")
  done < <(jq -c '.files[]?' <<<"${spec}")
}

run_container() {
  local spec="${1}"
  local key="${2}"
  local container_name="${3}"
  local spec_digest="${4}"
  local image_name restart_policy network_mode workdir
  local -a run_cmd=()
  local item_json

  image_name="$(jq -r '.image' <<<"${spec}")"
  restart_policy="$(jq -r '.restart' <<<"${spec}")"
  network_mode="$(jq -r '.network_mode' <<<"${spec}")"
  workdir="$(jq -r '.workdir' <<<"${spec}")"

  run_cmd=(
    docker run
    -d
    --name "${container_name}"
    --restart "${restart_policy}"
    --label "io.ha_pxe.managed=true"
    --label "io.ha_pxe.client_serial=${PXE_SERIAL}"
    --label "io.ha_pxe.container_key=${key}"
    --label "io.ha_pxe.spec_hash=${spec_digest}"
  )

  if [[ "$(jq -r '.privileged' <<<"${spec}")" == "true" ]]; then
    run_cmd+=(--privileged)
  fi

  if [[ -n "${network_mode}" ]]; then
    run_cmd+=(--network "${network_mode}")
  fi

  if [[ -n "${workdir}" ]]; then
    run_cmd+=(--workdir "${workdir}")
  fi

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(-e "$(jq -r '.key + "=" + .value' <<<"${item_json}")")
  done < <(jq -c '.env | to_entries[]?' <<<"${spec}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(--label "$(jq -r '.key + "=" + .value' <<<"${item_json}")")
  done < <(jq -c '.labels | to_entries[]?' <<<"${spec}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(--device "${item_json}")
  done < <(jq -r '.devices[]?' <<<"${spec}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(--add-host "${item_json}")
  done < <(jq -r '.extra_hosts[]?' <<<"${spec}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(-p "${item_json}")
  done < <(jq -r '.ports[]?' <<<"${spec}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=(-v "${item_json}")
  done < <(jq -r '.volumes[]?' <<<"${spec}")

  for item_json in "${HA_PXE_MATERIALIZED_FILE_MOUNTS[@]}"; do
    run_cmd+=(-v "${item_json}")
  done

  run_cmd+=("${image_name}")

  while IFS= read -r item_json; do
    [[ -n "${item_json}" ]] || continue
    run_cmd+=("${item_json}")
  done < <(jq -r '.command[]?' <<<"${spec}")

  log_info "Starting ${container_name}"
  "${run_cmd[@]}"
}

reconcile_container() {
  local spec="${1}"
  local key container_name state_dir desired_image_id desired_spec_hash current_spec_hash current_image_id

  key="$(spec_key "${spec}")"
  container_name="$(container_name_for_spec "${spec}")"
  state_dir="$(container_dir_for_spec "${spec}")"

  mkdir -p "${state_dir}"
  desired_image_id="$(ensure_desired_image "${spec}" "${key}" "${state_dir}")"
  materialize_files "${spec}" "${state_dir}"
  desired_spec_hash="$(spec_hash "${spec}")"
  current_spec_hash="$(docker inspect --format '{{ index .Config.Labels "io.ha_pxe.spec_hash" }}' "${container_name}" 2>/dev/null || true)"
  current_image_id="$(docker inspect --format '{{.Image}}' "${container_name}" 2>/dev/null || true)"

  if [[ -z "${current_image_id}" ]]; then
    run_container "${spec}" "${key}" "${container_name}" "${desired_spec_hash}"
    return 0
  fi

  if [[ "${current_image_id}" != "${desired_image_id}" || "${current_spec_hash}" != "${desired_spec_hash}" ]]; then
    log_info "Recreating ${container_name}"
    docker rm -f "${container_name}" >/dev/null
    run_container "${spec}" "${key}" "${container_name}" "${desired_spec_hash}"
    return 0
  fi

  docker start "${container_name}" >/dev/null 2>&1 || true
}

cleanup_stale_containers() {
  local -n desired_ref="${1}"
  local container_id existing_name existing_key

  while IFS= read -r container_id; do
    [[ -n "${container_id}" ]] || continue
    existing_key="$(docker inspect --format '{{ index .Config.Labels "io.ha_pxe.container_key" }}' "${container_id}" 2>/dev/null || true)"
    if [[ -n "${existing_key}" && -n "${desired_ref[${existing_key}]+set}" ]]; then
      continue
    fi

    existing_name="$(docker inspect --format '{{.Name}}' "${container_id}" | sed 's#^/##')"
    log_info "Removing stale container ${existing_name}"
    docker rm -f "${container_id}" >/dev/null || true
  done < <(docker ps -aq --filter label=io.ha_pxe.managed=true)
}

cleanup_stale_state_dirs() {
  local -n desired_ref="${1}"
  local state_dir key

  mkdir -p "${STATE_ROOT}"

  while IFS= read -r state_dir; do
    [[ -n "${state_dir}" ]] || continue
    key="${state_dir##*/}"
    if [[ -n "${desired_ref[${key}]+set}" ]]; then
      continue
    fi

    rm -rf "${state_dir}"
  done < <(find "${STATE_ROOT}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
}

main() {
  local spec key desired_json
  local had_error=false
  declare -A desired_keys=()

  if ! command -v docker >/dev/null 2>&1; then
    exit 0
  fi

  if ! jq -e 'type == "array"' "${DESIRED_FILE}" >/dev/null 2>&1; then
    log_error "Container spec file is invalid: ${DESIRED_FILE}"
    exit 1
  fi

  if ! desired_json="$(sort_container_specs_json "$(jq -cS '.' "${DESIRED_FILE}")")"; then
    exit 1
  fi

  ensure_docker_running
  mkdir -p "${STATE_ROOT}"

  while IFS= read -r spec; do
    [[ -n "${spec}" ]] || continue
    key="$(spec_key "${spec}")"
    desired_keys["${key}"]=1
  done < <(jq -c '.[]?' <<<"${desired_json}")

  while IFS= read -r spec; do
    [[ -n "${spec}" ]] || continue
    if ! reconcile_container "${spec}"; then
      log_error "Failed to reconcile $(jq -r '.name' <<<"${spec}")"
      had_error=true
    fi
  done < <(jq -c '.[]?' <<<"${desired_json}")

  cleanup_stale_containers desired_keys
  cleanup_stale_state_dirs desired_keys

  if [[ "${had_error}" == "true" ]]; then
    exit 1
  fi
}

main "$@"

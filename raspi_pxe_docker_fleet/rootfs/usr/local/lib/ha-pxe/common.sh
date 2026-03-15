#!/usr/bin/env bash
set -Eeuo pipefail

HA_PXE_ROOT="/data"
HA_PXE_OPTIONS_FILE="${HA_PXE_ROOT}/options.json"
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
HA_PXE_LOG_LEVEL="info"

ha_pxe::log_level_rank() {
  case "${1}" in
    error)
      printf '0\n'
      ;;
    warn)
      printf '1\n'
      ;;
    info)
      printf '2\n'
      ;;
    debug)
      printf '3\n'
      ;;
    *)
      printf '2\n'
      ;;
  esac
}

ha_pxe::should_log() {
  local requested_rank configured_rank

  requested_rank="$(ha_pxe::log_level_rank "${1}")"
  configured_rank="$(ha_pxe::log_level_rank "${HA_PXE_LOG_LEVEL}")"
  (( requested_rank <= configured_rank ))
}

ha_pxe::emit_log() {
  local level="${1}"
  shift

  ha_pxe::should_log "${level}" || return 0

  case "${level}" in
    error)
      if declare -F bashio::log.error >/dev/null 2>&1; then
        bashio::log.error "$*"
      else
        echo "[ERROR] $*" >&2
      fi
      ;;
    warn)
      if declare -F bashio::log.warning >/dev/null 2>&1; then
        bashio::log.warning "$*"
      else
        echo "[WARN] $*" >&2
      fi
      ;;
    debug)
      if declare -F bashio::log.info >/dev/null 2>&1; then
        bashio::log.info "[debug] $*"
      else
        echo "[DEBUG] $*" >&2
      fi
      ;;
    *)
      if declare -F bashio::log.info >/dev/null 2>&1; then
        bashio::log.info "$*"
      else
        echo "[INFO] $*" >&2
      fi
      ;;
  esac
}

ha_pxe::init_logging() {
  local configured_level

  configured_level="$(ha_pxe::config_string '.log_level')"
  case "${configured_level}" in
    error|warn|info|debug)
      HA_PXE_LOG_LEVEL="${configured_level}"
      ;;
    "")
      HA_PXE_LOG_LEVEL="info"
      ;;
    *)
      HA_PXE_LOG_LEVEL="info"
      ha_pxe::emit_log warn "Unsupported log_level '${configured_level}', defaulting to info"
      ;;
  esac

  ha_pxe::emit_log info "Configured log level: ${HA_PXE_LOG_LEVEL}"
}

ha_pxe::log_info() {
  ha_pxe::emit_log info "$@"
}

ha_pxe::log_warning() {
  ha_pxe::emit_log warn "$@"
}

ha_pxe::log_error() {
  ha_pxe::emit_log error "$@"
}

ha_pxe::log_debug() {
  ha_pxe::emit_log debug "$@"
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

ha_pxe::config_string() {
  local filter="${1}"

  jq -r "${filter} // empty" "${HA_PXE_OPTIONS_FILE}"
}

ha_pxe::config_json() {
  local filter="${1}"
  local default_json="${2:-null}"

  jq -c "${filter} // ${default_json}" "${HA_PXE_OPTIONS_FILE}"
}

ha_pxe::sort_container_specs_json() {
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
    ha_pxe::log_error "${sorted}"
    return 1
  fi

  printf '%s\n' "${sorted}"
}

ha_pxe::container_specs_json() {
  local raw="${1:-}"
  local normalized

  if ! normalized="$(
    jq -Rn -cS --arg raw "${raw}" '
      def slug:
        ascii_downcase
        | gsub("[^a-z0-9_.-]+"; "-")
        | gsub("^-+"; "")
        | gsub("-+$"; "")
        | if . == "" then "container" else . end;

      def normalize_key_values($value; $field):
        if $value == null then
          {}
        elif ($value | type) != "object" then
          error("\($field) must be an object")
        else
          $value
          | with_entries(select(.value != null) | .value |= tostring)
        end;

      def normalize_string_array($value; $field):
        if $value == null then
          []
        elif ($value | type) != "array" then
          error("\($field) must be an array")
        elif ([ $value[] | type == "string" ] | all) then
          $value
        else
          error("\($field) entries must be strings")
        end;

      def normalize_command($value):
        if $value == null then
          []
        elif ($value | type) == "array" then
          [ $value[] | tostring ]
        elif ($value | type) == "string" then
          [ $value ]
        else
          error("command must be a string or array")
        end;

      def normalize_depends_on($value):
        if $value == null then
          []
        elif ($value | type) == "array" then
          [ $value[] | tostring | slug ] | unique
        elif ($value | type) == "object" then
          [ $value | keys_unsorted[] | tostring | slug ] | unique
        else
          error("depends_on must be an array or object")
        end;

      def normalize_volumes($value):
        if $value == null then
          []
        elif ($value | type) != "array" then
          error("volumes must be an array")
        else
          [
            $value[]
            | if type == "string" then
                .
              elif type == "object" then
                (.source // .src // error("volume entry requires source")) as $source
                | (.target // .dst // .destination // error("volume entry requires target")) as $target
                | ($source + ":" + $target + (if (.read_only // false) then ":ro" else "" end))
              else
                error("volume entries must be strings or objects")
              end
          ]
        end;

      def normalize_ports($value):
        if $value == null then
          []
        elif ($value | type) != "array" then
          error("ports must be an array")
        else
          [
            $value[]
            | if type == "string" then
                .
              elif type == "object" then
                (.container // .target // error("port entry requires container")) as $container
                | (.host // .published // error("port entry requires host")) as $host
                | (.protocol // "tcp" | tostring) as $protocol
                | (($host | tostring) + ":" + ($container | tostring) + (if $protocol == "tcp" then "" else "/" + $protocol end))
              else
                error("port entries must be strings or objects")
              end
          ]
        end;

      def normalize_files($value):
        if $value == null then
          []
        elif ($value | type) != "array" then
          error("files must be an array")
        else
          [
            $value[]
            | if type != "object" then
                error("file entries must be objects")
              else
                .
              end
            | {
                container_path: (.container_path // .path // error("file entry requires container_path")),
                content: (.content // ""),
                format: (.format // (if ((.content // "") | type) == "string" then "text" else "json" end)),
                mode: ((.mode // "0644") | tostring),
                read_only: (.read_only // true)
              }
            | if (.container_path | type != "string" or (startswith("/") | not)) then
                error("file container_path must be absolute")
              else
                .
              end
          ]
        end;

      def parse_source_string($raw_source):
        if ($raw_source | test("^https?://.+\\.git(?:#.*)?$")) or ($raw_source | test("^git@.+:.+\\.git(?:#.*)?$")) then
          ($raw_source | capture("^(?<url>.+?\\.git)(?:#(?<fragment>.*))?$")) as $match
          | (($match.fragment // "") | split(":")) as $parts
          | {
              type: "git",
              url: $match.url,
              ref: (if ($parts | length) > 0 and ($parts[0] != "") then $parts[0] else "main" end),
              context: (if ($parts | length) > 1 and ($parts[1] != "") then $parts[1] else "." end),
              dockerfile: "Dockerfile",
              build_args: {}
            }
        elif ($raw_source | test("^https?://")) then
          {
            type: "dockerfile_url",
            url: $raw_source,
            dockerfile: "Dockerfile",
            build_args: {}
          }
        else
          {
            type: "image",
            ref: $raw_source
          }
        end;

      def normalize_source($value; $image_override):
        if $value == null and (($image_override // "") != "") then
          { type: "image", ref: $image_override }
        elif ($value | type) == "string" then
          parse_source_string($value)
        elif ($value | type) == "object" then
          if ($value.type // "") == "image" then
            {
              type: "image",
              ref: ($value.ref // $value.image // $image_override // error("image source requires ref"))
            }
          elif ($value.type // "") == "git" then
            {
              type: "git",
              url: ($value.url // error("git source requires url")),
              ref: ($value.ref // "main"),
              context: ($value.context // "."),
              dockerfile: ($value.dockerfile // "Dockerfile"),
              build_args: normalize_key_values(($value.build_args // {}); "source.build_args")
            }
          elif ($value.type // "") == "dockerfile_url" then
            {
              type: "dockerfile_url",
              url: ($value.url // error("dockerfile_url source requires url")),
              dockerfile: ($value.dockerfile // "Dockerfile"),
              build_args: normalize_key_values(($value.build_args // {}); "source.build_args")
            }
          else
            error("unsupported source.type")
          end
        else
          error("container source must be a string or object")
        end;

      def infer_name($source):
        (
          if $source.type == "image" then
            $source.ref
            | split("/")
            | last
            | split("@")[0]
            | split(":")[0]
          elif $source.type == "git" then
            $source.url
            | sub("#.*$"; "")
            | split("/")
            | last
            | sub("\\.git$"; "")
          else
            $source.url
            | split("?")[0]
            | split("#")[0]
            | split("/")
            | last
            | sub("\\.[A-Za-z0-9._-]+$"; "")
          end
        )
        | slug;

      def default_image($source; $name):
        if $source.type == "image" then
          $source.ref
        else
          "ha-pxe/" + $name + ":managed"
        end;

      def normalize_item:
        if type == "string" then
          { source: . }
        elif type == "object" then
          .
        else
          error("container entries must be strings or objects")
        end
        | . as $item
        | normalize_source(($item.source // null); ($item.image // null)) as $source
        | (($item.name // infer_name($source)) | tostring | slug) as $name
        | {
            name: $name,
            image: (
              ($item.image // null)
              | if . == null or . == "" then default_image($source; $name) else . end
            ),
            source: $source,
            restart: (($item.restart // "unless-stopped") | tostring),
            network_mode: (($item.network_mode // "") | tostring),
            privileged: ($item.privileged // false),
            workdir: (($item.workdir // "") | tostring),
            depends_on: normalize_depends_on(($item.depends_on // null)),
            env: normalize_key_values(($item.env // {}); "env"),
            labels: normalize_key_values(($item.labels // {}); "labels"),
            files: normalize_files($item.files // []),
            volumes: normalize_volumes($item.volumes // []),
            ports: normalize_ports($item.ports // []),
            devices: normalize_string_array(($item.devices // []); "devices"),
            extra_hosts: normalize_string_array(($item.extra_hosts // []); "extra_hosts"),
            command: normalize_command($item.command // null)
          }
        | if .source.type == "image" then
            .image = .source.ref
          else
            .
          end;

      def input_entries:
        ($raw | gsub("[[:space:]]"; "")) as $compact
        | if $compact == "" then
            []
          elif ($compact | startswith("[")) or ($compact | startswith("{")) then
            ($raw | fromjson) as $decoded
            | if ($decoded | type) == "array" then
                $decoded
              elif ($decoded | type) == "object" then
                [$decoded]
              else
                error("containers JSON must decode to an object or array")
              end
          else
            $raw
            | split("\n")
            | map(sub("\r$"; ""))
            | map(gsub("[[:space:]]+#.*$"; ""))
            | map(gsub("^[[:space:]]+|[[:space:]]+$"; ""))
            | map(select(length > 0))
          end;

      input_entries
      | map(normalize_item) as $items
      | if ([ $items[].name ] | length) == ([ $items[].name ] | unique | length) then
          $items
        else
          error("container names must be unique; set an explicit name when sources would infer the same one")
        end
    ' 2>/dev/null
  )"; then
    ha_pxe::log_error "Invalid containers configuration. Use newline-separated image or source URLs, or a JSON array of container specs."
    return 1
  fi

  if ! normalized="$(ha_pxe::sort_container_specs_json "${normalized}")"; then
    return 1
  fi

  printf '%s\n' "${normalized}"
}

ha_pxe::format_mib() {
  local bytes="${1:-0}"
  printf '%d' "$(((bytes + 1048575) / 1048576))"
}

ha_pxe::remote_content_length() {
  local url="${1}"

  curl -fsSLI "${url}" | awk '
    {
      key = tolower($1)
      if (key == "content-length:") {
        gsub(/\r/, "", $2)
        length = $2
      }
    }
    END {
      if (length ~ /^[0-9]+$/) {
        print length
      }
    }
  '
}

ha_pxe::require_mount_support() {
  local probe_dir probe_err err_text

  probe_dir="$(mktemp -d "${HA_PXE_TMP_DIR}/mount-check.XXXXXX")"
  probe_err="$(mktemp "${HA_PXE_TMP_DIR}/mount-check.XXXXXX.err")"

  if mount -t tmpfs -o size=1m tmpfs "${probe_dir}" 2>"${probe_err}"; then
    umount "${probe_dir}" || true
    rm -f "${probe_err}"
    rmdir "${probe_dir}" || true
    return 0
  fi

  err_text=""
  if [[ -s "${probe_err}" ]]; then
    err_text="$(tr '\n' ' ' < "${probe_err}" | sed -E 's/[[:space:]]+/ /g; s/[[:space:]]+$//')"
  fi

  rm -f "${probe_err}"
  rmdir "${probe_dir}" 2>/dev/null || true

  ha_pxe::log_error "Mount operations are blocked inside the add-on${err_text:+ (${err_text})}."
  ha_pxe::log_error "Disable Home Assistant Protection mode for this add-on and restart it. Mount privileges are required to unpack Raspberry Pi images and run NFS."
  return 1
}

ha_pxe::download_with_progress() {
  local url="${1}"
  local destination_path="${2}"
  local label="${3:-${destination_path##*/}}"
  local content_length current_bytes current_mib total_mib percent
  local last_percent=-5 last_bytes=0
  local curl_err curl_pid

  content_length="$(ha_pxe::remote_content_length "${url}" || true)"
  curl_err="$(mktemp "${HA_PXE_TMP_DIR}/curl.XXXXXX.err")"

  ha_pxe::log_info "Downloading ${label}"
  curl -fL --silent --show-error "${url}" -o "${destination_path}" 2>"${curl_err}" &
  curl_pid="$!"

  while kill -0 "${curl_pid}" 2>/dev/null; do
    sleep 5
    [[ -f "${destination_path}" ]] || continue

    current_bytes="$(wc -c < "${destination_path}")"
    current_bytes="${current_bytes//[[:space:]]/}"
    current_mib="$(ha_pxe::format_mib "${current_bytes}")"

    if [[ "${content_length}" =~ ^[0-9]+$ ]] && (( content_length > 0 )); then
      percent=$(( current_bytes * 100 / content_length ))
      if (( percent >= last_percent + 5 && percent < 100 )); then
        total_mib="$(ha_pxe::format_mib "${content_length}")"
        ha_pxe::log_info "Downloading ${label}: ${percent}% (${current_mib} MiB/${total_mib} MiB)"
        last_percent="${percent}"
      fi
    elif (( current_bytes >= last_bytes + 26214400 )); then
      ha_pxe::log_info "Downloading ${label}: ${current_mib} MiB received"
      last_bytes="${current_bytes}"
    fi
  done

  if ! wait "${curl_pid}"; then
    local err_text=""

    if [[ -s "${curl_err}" ]]; then
      err_text="$(tr '\n' ' ' < "${curl_err}" | sed -E 's/[[:space:]]+/ /g; s/[[:space:]]+$//')"
    fi

    rm -f "${curl_err}" "${destination_path}"
    ha_pxe::log_error "Download failed for ${label}${err_text:+: ${err_text}}"
    return 1
  fi

  rm -f "${curl_err}"
  current_bytes="$(wc -c < "${destination_path}")"
  current_bytes="${current_bytes//[[:space:]]/}"
  current_mib="$(ha_pxe::format_mib "${current_bytes}")"

  if [[ "${content_length}" =~ ^[0-9]+$ ]] && (( content_length > 0 )); then
    total_mib="$(ha_pxe::format_mib "${content_length}")"
    ha_pxe::log_info "Downloaded ${label}: 100% (${current_mib} MiB/${total_mib} MiB)"
  else
    ha_pxe::log_info "Downloaded ${label}: ${current_mib} MiB"
  fi
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
  local username password keys clients_json client_json client_serial containers_raw

  username="$(ha_pxe::config_string '.default_username')"
  password="$(ha_pxe::config_string '.default_password')"
  keys="$(ha_pxe::config_string '.ssh_authorized_keys')"
  clients_json="$(ha_pxe::config_json '.clients' '[]')"

  if [[ -z "${password}" && -z "${keys}" ]]; then
    ha_pxe::log_error "Set either default_password or ssh_authorized_keys so the client user can be accessed"
    return 1
  fi

  if [[ -n "${clients_json}" && "${clients_json}" != "null" ]]; then
    if ! jq -e '([.[].serial | ascii_downcase | ltrimstr("0x")] | length) == ([.[].serial | ascii_downcase | ltrimstr("0x")] | unique | length)' <<<"${clients_json}" >/dev/null; then
      ha_pxe::log_error "Client serial numbers must be unique"
      return 1
    fi

    while IFS= read -r client_json; do
      [[ -n "${client_json}" ]] || continue
      client_serial="$(jq -r '.serial' <<<"${client_json}")"
      containers_raw="$(jq -r '.containers // ""' <<<"${client_json}")"
      if ! ha_pxe::container_specs_json "${containers_raw}" >/dev/null; then
        ha_pxe::log_error "Client ${client_serial} has an invalid containers configuration"
        return 1
      fi
    done < <(jq -c '.[]?' <<<"${clients_json}")
  fi

  ha_pxe::log_info "Default client user is ${username}"
}

ha_pxe::resolve_server_ip() {
  local configured ip

  configured="$(ha_pxe::config_string '.server_ip')"
  if [[ -n "${configured}" ]]; then
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
    ha_pxe::download_with_progress "${url}" "${tmp_path}" "${url##*/}"
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

ha_pxe::cleanup_loop_devices_for_image() {
  local image_path="${1}"
  local loop_device

  while IFS= read -r loop_device; do
    [[ -n "${loop_device}" ]] || continue
    loop_device="${loop_device%:}"

    if findmnt -rn -o SOURCE | grep -q "^${loop_device}\\(p[0-9]\\+\\)\?$"; then
      ha_pxe::log_warning "Loop device ${loop_device} is still mounted; leaving it attached"
      continue
    fi

    ha_pxe::log_warning "Detaching stale loop device ${loop_device} for ${image_path##*/}"
    losetup -d "${loop_device}" || true
  done < <(losetup -j "${image_path}" | awk -F: '{print $1}')
}

ha_pxe::wait_for_block_device() {
  local block_device="${1}"
  local attempts="${2:-20}"
  local attempt=0

  while (( attempt < attempts )); do
    if [[ -b "${block_device}" ]]; then
      return 0
    fi
    sleep 0.5
    attempt=$((attempt + 1))
  done

  return 1
}

ha_pxe::populate_from_image() {
  local image_path="${1}"
  local boot_dir="${2}"
  local root_dir="${3}"
  local mount_boot mount_root loop_device boot_partition root_partition
  local mounted_boot=false mounted_root=false loop_attached=false

  cleanup_mounts() {
    if [[ "${mounted_root}" == "true" ]]; then
      umount "${mount_root}" || true
    fi
    if [[ "${mounted_boot}" == "true" ]]; then
      umount "${mount_boot}" || true
    fi
    if [[ "${loop_attached}" == "true" ]]; then
      losetup -d "${loop_device}" || true
    fi
    rmdir "${mount_boot}" "${mount_root}" 2>/dev/null || true
  }

  mount_boot="$(mktemp -d "${HA_PXE_TMP_DIR}/boot.XXXXXX")"
  mount_root="$(mktemp -d "${HA_PXE_TMP_DIR}/root.XXXXXX")"
  trap cleanup_mounts RETURN

  ha_pxe::log_debug "Preparing loop device for ${image_path##*/}"
  ha_pxe::cleanup_loop_devices_for_image "${image_path}"

  if ! loop_device="$(losetup --find --show --read-only --partscan "${image_path}")"; then
    ha_pxe::log_error "Failed to create a loop device for ${image_path}"
    return 1
  fi
  loop_attached=true
  boot_partition="${loop_device}p1"
  root_partition="${loop_device}p2"

  ha_pxe::log_debug "Attached ${image_path##*/} to ${loop_device}"
  ha_pxe::log_debug "Waiting for partition devices ${boot_partition} and ${root_partition}"
  if ! ha_pxe::wait_for_block_device "${boot_partition}" || ! ha_pxe::wait_for_block_device "${root_partition}"; then
    ha_pxe::log_error "Partition devices for ${loop_device} did not appear"
    return 1
  fi

  ha_pxe::log_debug "Mounting boot partition ${boot_partition} to ${mount_boot}"
  if ! mount -o ro -t vfat "${boot_partition}" "${mount_boot}"; then
    ha_pxe::log_error "Failed to mount boot partition ${boot_partition} from ${image_path}"
    return 1
  fi
  mounted_boot=true

  ha_pxe::log_debug "Mounting root partition ${root_partition} to ${mount_root}"
  if ! mount -o ro -t ext4 "${root_partition}" "${mount_root}"; then
    ha_pxe::log_error "Failed to mount root partition ${root_partition} from ${image_path}"
    return 1
  fi
  mounted_root=true

  ha_pxe::log_debug "Clearing target boot export ${boot_dir}"
  ha_pxe::clear_directory "${boot_dir}"
  ha_pxe::log_debug "Clearing target root export ${root_dir}"
  ha_pxe::clear_directory "${root_dir}"

  ha_pxe::log_debug "Syncing boot files into ${boot_dir}"
  rsync -a --delete "${mount_boot}/" "${boot_dir}/"
  ha_pxe::log_debug "Syncing root filesystem into ${root_dir}"
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

ha_pxe::model_needs_root_bootcode() {
  case "${1}" in
    pi2|pi3|cm3)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ha_pxe::publish_root_tftp_firmware() {
  local boot_dir="${1}"
  local serial="${2}"
  local model="${3}"
  local file_name source_path target_path

  if ! ha_pxe::model_needs_root_bootcode "${model}"; then
    ha_pxe::log_debug "Skipping shared root TFTP firmware for ${serial}; model ${model} uses EEPROM boot and should fetch prefixed start*.elf files instead"
    return 0
  fi

  for file_name in bootcode.bin bootsig.bin; do
    source_path="${boot_dir}/${file_name}"
    target_path="${HA_PXE_TFTP_DIR}/${file_name}"

    if [[ ! -f "${source_path}" ]]; then
      if [[ "${file_name}" == "bootsig.bin" ]]; then
        ha_pxe::log_debug "No ${file_name} present in ${boot_dir}; Raspberry Pi ROMs commonly probe for it and usually accept file-not-found"
      fi
      continue
    fi

    if [[ ! -f "${target_path}" ]] || ! cmp -s "${source_path}" "${target_path}"; then
      cp -f "${source_path}" "${target_path}"
      chmod 0644 "${target_path}"
      ha_pxe::log_info "Publishing shared TFTP ${file_name} from ${serial}"
    else
      ha_pxe::log_debug "Shared TFTP ${file_name} already matches client ${serial}"
    fi
  done
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
  local dnsmasq_args=(
    --keep-in-foreground
    --port=0
    --enable-tftp
    --tftp-root="${HA_PXE_TFTP_DIR}"
    --tftp-no-fail
    --log-facility=-
    --quiet-dhcp
    --quiet-dhcp6
    --quiet-ra
    --bind-interfaces
  )

  case "${HA_PXE_LOG_LEVEL}" in
    debug)
      dnsmasq_args+=( --log-debug )
      ha_pxe::log_info "TFTP request logging is enabled via dnsmasq"
      ;;
    *)
      dnsmasq_args+=( --quiet-tftp )
      ;;
  esac

  ha_pxe::log_debug "Starting dnsmasq TFTP server with root ${HA_PXE_TFTP_DIR}"
  dnsmasq "${dnsmasq_args[@]}" &
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

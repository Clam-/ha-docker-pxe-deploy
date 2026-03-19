"""Docker reconciliation helpers for provisioned clients."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..container_specs import sort_container_specs
from ..errors import HaPxeError, SpecError
from ..fs_utils import atomic_write, clear_directory, ensure_directory
from ..shell import run
from ..text import slug, stable_json
from .logging import ClientLogger


@dataclass
class BuildInputs:
    context_dir: Path
    dockerfile_path: Path
    fingerprint: str


def load_desired_specs(path: Path) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # noqa: PERF203
        raise HaPxeError(f"Container spec file is invalid: {path}") from exc
    if not isinstance(decoded, list):
        raise HaPxeError(f"Container spec file is invalid: {path}")
    try:
        return sort_container_specs(decoded)
    except SpecError as exc:
        raise HaPxeError(str(exc)) from exc


def ensure_docker_running(logger: ClientLogger) -> None:
    logger.info("Starting docker.service before reconciling managed containers")
    run(["systemctl", "start", "docker.service"])
    logger.info("docker.service is running")


def spec_key(spec: dict[str, Any]) -> str:
    identity = stable_json({"name": spec["name"], "source": spec["source"]})
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"{slug(spec['name'])}-{digest}"


def container_name_for_spec(spec: dict[str, Any]) -> str:
    return f"ha-pxe-{spec_key(spec)}"


def container_dir_for_spec(state_root: Path, spec: dict[str, Any]) -> Path:
    return state_root / spec_key(spec)


def spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(spec).encode("utf-8")).hexdigest()


def reconcile_container(
    spec: dict[str, Any],
    state_root: Path,
    logger: ClientLogger,
    serial: str,
) -> None:
    key = spec_key(spec)
    container_name = container_name_for_spec(spec)
    state_dir = container_dir_for_spec(state_root, spec)
    display_name = str(spec["name"])
    stage_name = f"reconcile-{slug(display_name)}"

    logger.stage_start(stage_name, f"Reconciling container {display_name}")
    try:
        ensure_directory(state_dir)
        logger.info(f"State directory for {display_name} is {state_dir}")

        desired_image_id = ensure_desired_image(spec, key, state_dir, logger, serial)
        file_mounts = materialize_files(spec, state_dir, logger)
        desired_spec_hash = spec_hash(spec)
        current_spec_hash = _docker_inspect(container_name, "{{ index .Config.Labels \"io.ha_pxe.spec_hash\" }}")
        current_image_id = _docker_inspect(container_name, "{{.Image}}")
        current_state = _docker_inspect(container_name, "{{.State.Status}}")

        if not current_image_id:
            logger.info(f"Container {container_name} does not exist yet; creating it")
            run_container(spec, key, container_name, desired_spec_hash, file_mounts, logger, serial)
            logger.stage_complete(stage_name, f"Created container {display_name}")
            return

        if current_image_id != desired_image_id or current_spec_hash != desired_spec_hash:
            logger.info(f"Recreating {container_name}")
            run(["docker", "rm", "-f", container_name])
            run_container(spec, key, container_name, desired_spec_hash, file_mounts, logger, serial)
            logger.stage_complete(stage_name, f"Recreated container {display_name} with updated image or spec")
            return

        if current_state != "running":
            logger.info(f"Container {container_name} exists but is not running; attempting to start it")
        else:
            logger.info(f"Container {container_name} is already up to date")
        if run(["docker", "start", container_name], check=False).returncode != 0:
            raise HaPxeError(f"Container {display_name} exists but Docker could not start it")
        logger.stage_complete(stage_name, f"Container {display_name} matches the desired state")
    except Exception as exc:
        logger.stage_fail(stage_name, f"Failed to reconcile container {display_name}: {exc}")
        raise


def cleanup_stale_containers(desired_keys: dict[str, int], logger: ClientLogger, serial: str) -> None:
    for container_id in _docker_ps(serial):
        existing_key = _docker_inspect_raw(container_id, "{{with index .Config.Labels \"io.ha_pxe.container_key\"}}{{.}}{{end}}")
        existing_serial = _docker_inspect_raw(container_id, "{{with index .Config.Labels \"io.ha_pxe.client_serial\"}}{{.}}{{end}}")
        if existing_key and existing_key in desired_keys:
            continue
        existing_name = _docker_inspect_raw(container_id, "{{.Name}}").lstrip("/")
        if existing_key:
            reason = f"container key {existing_key} is not present in the desired key set"
        else:
            reason = "missing io.ha_pxe.container_key label"
        logger.info(
            f"Removing stale container {existing_name or container_id} (id={container_id} key={existing_key or 'missing'} serial={existing_serial or 'missing'} reason={reason})"
        )
        run(["docker", "rm", "-f", container_id], check=False)


def cleanup_stale_state_dirs(state_root: Path, desired_keys: dict[str, int], logger: ClientLogger) -> None:
    ensure_directory(state_root)
    for state_dir in sorted(path for path in state_root.iterdir() if path.is_dir()):
        key = state_dir.name
        if key in desired_keys:
            continue
        logger.info(f"Removing stale state directory {state_dir} (key={key} reason=state directory key is not present in the desired key set)")
        shutil.rmtree(state_dir)


def describe_desired_keys(desired_keys: dict[str, int], desired_names: dict[str, str]) -> str:
    if not desired_keys:
        return "none"
    return "; ".join(f"{desired_names.get(key, 'unknown')}[key={key}]" for key in sorted(desired_keys))


def describe_managed_containers(serial: str) -> str:
    entries: list[str] = []
    for container_id in _docker_ps(serial):
        details = _docker_inspect_raw(
            container_id,
            "{{.Name}}|{{with index .Config.Labels \"io.ha_pxe.container_key\"}}{{.}}{{end}}|{{with index .Config.Labels \"io.ha_pxe.client_serial\"}}{{.}}{{end}}|{{.State.Status}}",
        )
        if not details:
            continue
        name, key, existing_serial, state = details.split("|", 3)
        entries.append(f"{name.lstrip('/')}[key={key or 'missing'},serial={existing_serial or 'missing'},state={state or 'unknown'}]")
    return "; ".join(entries) if entries else "none"


def describe_state_dirs(state_root: Path) -> str:
    ensure_directory(state_root)
    entries = [path.name for path in sorted(state_root.iterdir()) if path.is_dir()]
    return ", ".join(entries) if entries else "none"


def ensure_desired_image(spec: dict[str, Any], key: str, state_dir: Path, logger: ClientLogger, serial: str) -> str:
    source_type = spec["source"]["type"]
    image_name = str(spec["image"])
    if source_type == "image":
        logger.info(f"Pulling {image_name}")
        completed = run(["docker", "pull", image_name], check=False, capture_output=True)
        if completed.returncode != 0:
            detail = _command_failure_detail(completed.stdout, completed.stderr)
            if detail:
                raise HaPxeError(f"Failed to pull {image_name}: {detail}")
            raise HaPxeError(f"Failed to pull {image_name}: docker pull exited with status {completed.returncode}")
        logger.info(f"Pulled {image_name} successfully")
        image_id = _docker_image_inspect(image_name, "{{.Id}}")
        if not image_id:
            raise HaPxeError(f"Pulled {image_name} but Docker cannot inspect the image locally")
        return image_id
    if source_type == "git":
        build = prepare_git_build(spec, state_dir, logger)
        return build_image_if_needed(spec, key, state_dir, build, logger, serial)
    if source_type == "dockerfile_url":
        build = prepare_remote_dockerfile_build(spec, state_dir, logger)
        return build_image_if_needed(spec, key, state_dir, build, logger, serial)
    raise HaPxeError(f"Unsupported source type: {source_type}")


def prepare_git_build(spec: dict[str, Any], state_dir: Path, logger: ClientLogger) -> BuildInputs:
    source = spec["source"]
    repo_dir = state_dir / "source" / "repo"
    ensure_directory(repo_dir.parent)
    logger.info(
        f"Preparing git build context from {source['url']} ref={source['ref']} context={source['context']} dockerfile={source['dockerfile']}"
    )
    if not (repo_dir / ".git").exists():
        logger.info(f"Cloning {source['url']} into the managed build cache")
        shutil.rmtree(repo_dir, ignore_errors=True)
        run(["git", "clone", "--no-checkout", source["url"], str(repo_dir)])
    run(["git", "-C", str(repo_dir), "remote", "set-url", "origin", source["url"]])
    logger.info(f"Fetching latest refs for {source['url']}")
    run(["git", "-C", str(repo_dir), "fetch", "--tags", "--prune", "origin"])
    run(["git", "-C", str(repo_dir), "fetch", "origin", source["ref"]], check=False)
    target = source["ref"]
    if run(["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{source['ref']}"], check=False).returncode == 0:
        target = f"origin/{source['ref']}"
    elif run(["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/tags/{source['ref']}"], check=False).returncode == 0:
        target = source["ref"]
    run(["git", "-C", str(repo_dir), "checkout", "--force", target])
    run(["git", "-C", str(repo_dir), "reset", "--hard", target])
    run(["git", "-C", str(repo_dir), "clean", "-fdx"])
    revision = _capture(["git", "-C", str(repo_dir), "rev-parse", "HEAD"])
    logger.info(f"Checked out revision {revision} for {source['url']}")

    context_dir = _resolve_relative_path(repo_dir, source["context"], "git build context")
    dockerfile_path = _resolve_relative_path(repo_dir, source["dockerfile"], "git Dockerfile path")
    if not context_dir.is_dir():
        raise HaPxeError(f"Git build context does not exist: {context_dir}")
    if not dockerfile_path.is_file():
        raise HaPxeError(f"Git Dockerfile does not exist: {dockerfile_path}")

    fingerprint_input = "\n".join((revision, source["context"], source["dockerfile"], stable_json(source["build_args"]), str(spec["image"])))
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    return BuildInputs(context_dir=context_dir, dockerfile_path=dockerfile_path, fingerprint=fingerprint)


def prepare_remote_dockerfile_build(spec: dict[str, Any], state_dir: Path, logger: ClientLogger) -> BuildInputs:
    source = spec["source"]
    source_dir = state_dir / "source" / "remote-dockerfile"
    context_dir = source_dir / "context"
    dockerfile_path = _resolve_relative_path(source_dir, source["dockerfile"], "remote Dockerfile path")
    ensure_directory(context_dir)
    ensure_directory(dockerfile_path.parent)
    logger.info(f"Downloading remote Dockerfile from {source['url']} into {dockerfile_path}")
    with urllib.request.urlopen(source["url"], timeout=30) as response:
        dockerfile_path.write_bytes(response.read())
    dockerfile_hash = hashlib.sha256(dockerfile_path.read_bytes()).hexdigest()
    fingerprint_input = "\n".join((source["url"], dockerfile_hash, stable_json(source["build_args"])))
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    return BuildInputs(context_dir=context_dir, dockerfile_path=dockerfile_path, fingerprint=fingerprint)


def build_image_if_needed(
    spec: dict[str, Any],
    key: str,
    state_dir: Path,
    build: BuildInputs,
    logger: ClientLogger,
    serial: str,
) -> str:
    image_name = str(spec["image"])
    build_log = state_dir / "build.log"
    existing_fingerprint = _docker_image_inspect(image_name, "{{ index .Config.Labels \"io.ha_pxe.build_fingerprint\" }}")
    if existing_fingerprint != build.fingerprint:
        command = [
            "docker",
            "build",
            "-t",
            image_name,
            "--label",
            "io.ha_pxe.managed=true",
            "--label",
            f"io.ha_pxe.client_serial={serial}",
            "--label",
            f"io.ha_pxe.container_key={key}",
            "--label",
            f"io.ha_pxe.build_fingerprint={build.fingerprint}",
            "-f",
            str(build.dockerfile_path),
        ]
        for build_arg_key, build_arg_value in spec["source"]["build_args"].items():
            command.extend(["--build-arg", f"{build_arg_key}={build_arg_value}"])
        command.append(str(build.context_dir))
        build_log.write_text("", encoding="utf-8")
        logger.info(f"Building {image_name}; detailed build output is being written to {build_log}")
        with build_log.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(command, check=False, text=True, stdout=handle, stderr=subprocess.STDOUT)
        if completed.returncode != 0:
            logger.error(f"Build failed for {image_name}; detailed build output is available at {build_log}")
            tail = build_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            for line in tail:
                print(line, file=sys.stderr)
            raise HaPxeError(f"Build failed for {image_name}")
        logger.info(f"Built {image_name} with updated fingerprint {build.fingerprint}")
    else:
        logger.info(f"Reusing existing image {image_name}; build fingerprint already matches")

    image_id = _docker_image_inspect(image_name, "{{.Id}}")
    if not image_id:
        raise HaPxeError(f"Docker cannot inspect the image {image_name}")
    return image_id


def materialize_files(spec: dict[str, Any], state_dir: Path, logger: ClientLogger) -> list[str]:
    files_dir = state_dir / "files"
    clear_directory(files_dir)
    logger.info(f"Materializing generated container files into {files_dir}")
    mounts: list[str] = []
    for file_spec in spec.get("files", []):
        container_path = str(file_spec["container_path"])
        host_path = files_dir / container_path.lstrip("/")
        ensure_directory(host_path.parent)
        file_format = str(file_spec["format"])
        if file_format == "json":
            content = json.dumps(file_spec.get("content"), sort_keys=True, indent=2) + "\n"
        elif file_format == "text":
            content_value = file_spec.get("content", "")
            content = content_value if isinstance(content_value, str) else str(content_value)
        else:
            raise HaPxeError(f"Unsupported file format '{file_format}' for {container_path}")
        atomic_write(host_path, content, int(str(file_spec["mode"]), 8))
        volume = f"{host_path}:{container_path}"
        if bool(file_spec.get("read_only", True)):
            volume = f"{volume}:ro"
        mounts.append(volume)
        logger.info(f"Prepared generated file mount {container_path} ({file_spec['mode']})")
    logger.info(f"Materialized {len(mounts)} generated file mount(s)")
    return mounts


def run_container(
    spec: dict[str, Any],
    key: str,
    container_name: str,
    spec_digest: str,
    file_mounts: list[str],
    logger: ClientLogger,
    serial: str,
) -> None:
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--restart",
        str(spec["restart"]),
        "--label",
        "io.ha_pxe.managed=true",
        "--label",
        f"io.ha_pxe.client_serial={serial}",
        "--label",
        f"io.ha_pxe.container_key={key}",
        "--label",
        f"io.ha_pxe.spec_hash={spec_digest}",
    ]
    if spec.get("privileged"):
        command.append("--privileged")
    if spec.get("network_mode"):
        command.extend(["--network", str(spec["network_mode"])])
    if spec.get("workdir"):
        command.extend(["--workdir", str(spec["workdir"])])
    for env_key, env_value in spec.get("env", {}).items():
        command.extend(["-e", f"{env_key}={env_value}"])
    for label_key, label_value in spec.get("labels", {}).items():
        command.extend(["--label", f"{label_key}={label_value}"])
    for device in spec.get("devices", []):
        command.extend(["--device", str(device)])
    for host in spec.get("extra_hosts", []):
        command.extend(["--add-host", str(host)])
    for port in spec.get("ports", []):
        command.extend(["-p", str(port)])
    for volume in spec.get("volumes", []):
        command.extend(["-v", str(volume)])
    for volume in file_mounts:
        command.extend(["-v", volume])
    command.append(str(spec["image"]))
    for arg in spec.get("command", []):
        command.append(str(arg))
    logger.info(f"Starting {container_name} from {spec['image']}")
    run(command)


def _resolve_relative_path(base_dir: Path, relative_path: str, kind: str) -> Path:
    if not relative_path or relative_path == ".":
        return base_dir
    if relative_path.startswith("/"):
        raise HaPxeError(f"{kind} must be relative: {relative_path}")
    candidate = (base_dir / relative_path).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved not in candidate.parents and candidate != base_resolved:
        raise HaPxeError(f"{kind} must stay inside the managed source tree: {relative_path}")
    return candidate


def _docker_ps(serial: str) -> list[str]:
    output = _capture(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=io.ha_pxe.managed=true",
            "--filter",
            f"label=io.ha_pxe.client_serial={serial}",
        ]
    )
    return [line for line in output.splitlines() if line]


def _docker_inspect(container: str, template: str) -> str:
    return _docker_inspect_raw(container, template)


def _docker_image_inspect(image: str, template: str) -> str:
    return _capture_optional(["docker", "image", "inspect", "--format", template, image])


def _docker_inspect_raw(target: str, template: str) -> str:
    return _capture_optional(["docker", "inspect", "--format", template, target])


def _capture(command: list[str]) -> str:
    completed = run(command, capture_output=True)
    return completed.stdout.strip()


def _capture_optional(command: list[str]) -> str:
    completed = run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _command_failure_detail(stdout: str, stderr: str) -> str:
    detail = sanitize_message(stderr) or sanitize_message(stdout)
    return detail

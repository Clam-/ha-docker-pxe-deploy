"""Early boot diagnostics entrypoint."""

from __future__ import annotations

from pathlib import Path

from ..shell import capture_optional, command_exists
from .bootstrap import BootstrapConfig, ClientPaths, configure_ssh_keys
from .logging import ClientLogger


def main() -> int:
    config = BootstrapConfig.load()
    logger = ClientLogger(config, prefix="ha-pxe-early-log", source="earlyboot")
    paths = ClientPaths()

    try:
        logger.emit_local("info", "startup", "started", f"Starting early boot diagnostics for {config.hostname} ({config.serial})")
        _wait_for_transport(logger)

        logger.stage_start("startup", f"Collecting early boot diagnostics for {config.hostname} ({config.serial})")
        boot_id = _read_boot_id()
        route = _route_summary()
        addresses = _address_summary()
        marker_present = "yes" if paths.firstboot_marker.exists() else "no"
        marker_present2 = "yes" if paths.bootstrap_env.exists() else "no"
        docker_present = "yes" if command_exists("docker") else "no"
        logger.info(
            f"boot_id={boot_id} route={route} ipv4={addresses} marker_present={marker_present} marker_present2={marker_present2} docker_present={docker_present}"
        )
        logger.stage_complete("startup", "Early boot diagnostics captured")
        configure_ssh_keys(config, logger, paths)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.fail_exception(exc)
        return 1


def _wait_for_transport(logger: ClientLogger) -> None:
    import time

    for attempt in range(1, 13):
        if logger.emit_remote("info", "startup", "reachable", f"Early boot diagnostics reached the add-on log transport on attempt {attempt}"):
            return
        time.sleep(5)


def _read_boot_id() -> str:
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    if boot_id_path.exists():
        return boot_id_path.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


def _route_summary() -> str:
    output = capture_optional(["ip", "-4", "route", "show", "default"])
    return output.splitlines()[0] if output else "none"


def _address_summary() -> str:
    output = capture_optional(["ip", "-4", "-brief", "address", "show", "up"])
    entries: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            entries.append(f"{parts[0]}={parts[2]}")
    return ";".join(entries) if entries else "none"

"""First boot provisioning entrypoint for PXE clients."""

from __future__ import annotations

import os
from pathlib import Path

from ..shell import command_exists, run
from .bootstrap import BootstrapConfig, ClientPaths, clear_stock_ssh_banner, configure_ssh_keys
from .locale_setup import apply_locale_defaults
from .logging import ClientLogger


def main() -> int:
    config = BootstrapConfig.load()
    paths = ClientPaths()
    logger = ClientLogger(config, prefix="ha-pxe-firstboot", source="firstboot")

    try:
        Path("/var/lib/ha-pxe").mkdir(parents=True, exist_ok=True)

        logger.stage_start("preflight", f"Starting first-boot provisioning for {config.hostname} ({config.serial})")
        if paths.firstboot_marker.exists():
            logger.stage_skip("preflight", "First-boot marker already exists; nothing to do")
            return 0
        logger.stage_complete("preflight", "First-boot marker is absent; continuing with provisioning")

        logger.stage_start("identity", "Configuring hostname and default user metadata")
        _configure_identity(config, logger)
        logger.stage_complete("identity", "Hostname and default user configuration complete")

        logger.stage_start("locale-defaults", "Applying non-interactive locale, timezone, and keyboard defaults")
        apply_locale_defaults(config, logger)
        logger.stage_complete("locale-defaults", "Locale, timezone, and keyboard defaults applied")

        logger.stage_start("packages", "Installing Docker and bootstrap dependencies")
        _install_packages(logger)
        logger.stage_complete("packages", "Base packages for Docker workloads were installed")

        logger.stage_start("access", "Applying group memberships and SSH access settings")
        _ensure_group_memberships(config, logger)
        configure_ssh_keys(config, logger, paths)
        clear_stock_ssh_banner(config, logger)
        logger.stage_complete("access", "User access configuration completed")

        logger.stage_start("services", "Enabling and starting Docker, SSH, and container-sync services")
        _configure_services(logger)
        logger.stage_complete("services", "Runtime services are enabled and started")

        logger.stage_start("finalize", "Recording first-boot completion marker")
        paths.firstboot_marker.touch()
        logger.stage_complete("finalize", "First-boot provisioning completed successfully")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.fail_exception(exc)
        return 1


def _configure_identity(config: BootstrapConfig, logger: ClientLogger) -> None:
    if run(["hostnamectl", "set-hostname", config.hostname], check=False).returncode == 0:
        logger.info(f"hostnamectl updated the transient hostname to {config.hostname}")
    else:
        logger.warning("hostnamectl could not update the transient hostname; continuing with file-based hostname changes")

    hostname_path = Path("/etc/hostname")
    if not hostname_path.exists() or hostname_path.read_text(encoding="utf-8").strip() != config.hostname:
        hostname_path.write_text(f"{config.hostname}\n", encoding="utf-8")
        logger.info(f"/etc/hostname updated to {config.hostname}")

    hosts_path = Path("/etc/hosts")
    if hosts_path.exists():
        lines = hosts_path.read_text(encoding="utf-8").splitlines()
        updated = False
        for index, line in enumerate(lines):
            if line.startswith("127.0.1.1") and line.split():
                lines[index] = f"127.0.1.1\t{config.hostname}"
                updated = True
                break
        if not updated:
            lines.append(f"127.0.1.1\t{config.hostname}")
        hosts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"/etc/hosts now maps 127.0.1.1 to {config.hostname}")

    if run(["id", config.username], check=False).returncode != 0:
        run(["useradd", "-m", "-s", "/bin/bash", config.username])
        logger.info(f"Created default user {config.username}")
    else:
        logger.info(f"Default user {config.username} already exists")

    if config.password_hash:
        run(["usermod", "-p", config.password_hash, config.username])
        logger.info(f"Applied the configured password hash for {config.username}")
    else:
        run(["passwd", "-l", config.username], check=False)
        logger.info(f"Locked the password for {config.username}; SSH key authentication is expected")


def _install_packages(logger: ClientLogger) -> None:
    os.environ.update(
        {
            "DEBIAN_FRONTEND": "noninteractive",
            "DEBCONF_FRONTEND": "noninteractive",
            "DEBCONF_NONINTERACTIVE_SEEN": "true",
            "DEBIAN_PRIORITY": "critical",
            "NEEDRESTART_MODE": "a",
        }
    )
    logger.info("Refreshing apt package indexes")
    run(["apt-get", "update"])
    logger.info("Installing ca-certificates, docker.io, docker-cli, and git")
    run(
        [
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            "Dpkg::Options::=--force-confold",
            "ca-certificates",
            "docker.io",
            "docker-cli",
            "git",
        ]
    )


def _ensure_group_memberships(config: BootstrapConfig, logger: ClientLogger) -> None:
    groups = [group for group in config.extra_groups.split(",") if group]
    added_groups = 0
    skipped_groups = 0
    for group in groups:
        if run(["getent", "group", group], check=False).returncode == 0:
            run(["usermod", "-aG", group, config.username], check=False)
            added_groups += 1
        else:
            skipped_groups += 1
            logger.info(f"Group {group} is not present on the client; skipping membership")
    logger.info(f"Group membership reconciliation complete: added={added_groups} missing={skipped_groups}")


def _configure_services(logger: ClientLogger) -> None:
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "docker.service"])
    logger.info("Enabled docker.service")
    if run(["systemctl", "enable", "containerd.service"], check=False).returncode == 0:
        logger.info("Enabled containerd.service")
    else:
        logger.warning("containerd.service could not be enabled; continuing")
    if run(["systemctl", "enable", "ssh.service"], check=False).returncode == 0:
        logger.info("Enabled ssh.service")
    else:
        logger.warning("ssh.service could not be enabled; continuing")
    run(["systemctl", "enable", "ha-pxe-container-sync.timer"])
    logger.info("Enabled ha-pxe-container-sync.timer")

    run(["systemctl", "start", "docker.service"])
    logger.info("Started docker.service")
    if run(["systemctl", "start", "ssh.service"], check=False).returncode == 0:
        logger.info("Started ssh.service")
    else:
        logger.warning("ssh.service could not be started; continuing")
    if run(["systemctl", "try-reload-or-restart", "ssh.service"], check=False).returncode == 0:
        logger.info("Reloaded or restarted ssh.service")
    else:
        logger.warning("ssh.service could not be reloaded or restarted cleanly")
    if run(["systemctl", "start", "ha-pxe-container-sync.service"], check=False).returncode == 0:
        logger.info("Triggered an initial ha-pxe-container-sync.service run")
    else:
        logger.warning("Initial ha-pxe-container-sync.service run failed to start; the recurring timer will retry")
    run(["systemctl", "start", "ha-pxe-container-sync.timer"])
    logger.info("Started ha-pxe-container-sync.timer")

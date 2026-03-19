"""Client export provisioning for the add-on."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .addon_context import AddonContext
from .container_specs import normalize_container_specs, specs_to_json
from .envfile import format_env_file
from .errors import HaPxeError
from .fs_utils import atomic_write, copy_file, copy_tree, ensure_directory, replace_symlink
from .image_ops import download_image, latest_image_url, populate_from_image
from .runtime import (
    append_exports,
    bind_tftp_tree,
    image_arch_for_model,
    normalize_serial,
    publish_root_tftp_firmware,
    validate_model,
    warn_if_model_needs_manual_attention,
    write_client_state,
)
from .shell import capture


DEFAULT_GROUPS = [
    "sudo",
    "adm",
    "dialout",
    "cdrom",
    "audio",
    "video",
    "plugdev",
    "users",
    "input",
    "netdev",
    "gpio",
    "i2c",
    "spi",
    "render",
    "docker",
]

CMDLINE_DROP_PREFIXES = (
    "root=",
    "rootfstype=",
    "rootflags=",
    "nfsroot=",
    "ip=",
    "init=",
    "rdinit=",
    "systemd.run=",
    "systemd.run_success_action=",
    "systemd.unit=",
)

CMDLINE_DROP_TOKENS = {"rootwait", "rw", "ro", "resize"}
BOOT_CONFIG_MANAGED_START = "# HA-PXE managed config start"
BOOT_CONFIG_MANAGED_END = "# HA-PXE managed config end"
BOOT_CONFIG_RESET_SECTION = "[all]"


def provision_client(context: AddonContext, client: dict[str, object], server_ip: str) -> None:
    serial = normalize_serial(str(client.get("serial", "")))
    short_serial = serial[-8:]
    model = str(client.get("model", ""))
    hostname = str(client.get("hostname", ""))
    arch_override = str(client.get("image_arch", "auto") or "auto")
    rebuild = bool(client.get("rebuild", False))
    containers_raw = str(client.get("containers", "") or "")
    containers = normalize_container_specs(containers_raw, context.mqtt_env_defaults())
    container_count = len(containers)

    if not validate_model(model):
        raise HaPxeError(f"Unsupported model '{model}' for client {serial}")

    warn_if_model_needs_manual_attention(context, model)
    arch = image_arch_for_model(model, arch_override)
    _log_stage(context, "info", serial, "prepare", "started", f"Provisioning {hostname} for model {model} with image arch {arch} and {container_count} managed container(s)")

    boot_dir = context.paths.exports_dir / serial / "boot"
    root_dir = context.paths.exports_dir / serial / "root"
    state_file = context.paths.state_dir / f"{serial}.json"
    ensure_directory(boot_dir)
    ensure_directory(root_dir)

    if rebuild:
        _log_stage(context, "warn", serial, "prepare", "in_progress", "Rebuild requested; clearing exported boot/root trees and cached state")
        from .fs_utils import clear_directory

        clear_directory(boot_dir)
        clear_directory(root_dir)
        state_file.unlink(missing_ok=True)

    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        existing_model = str(state.get("model", "") or "")
        existing_arch = str(state.get("arch", "") or "")
        if existing_model and existing_model != model:
            context.logger.warning(
                f"Client {serial} model changed from {existing_model} to {model}; set rebuild: true to refresh the exported rootfs"
            )
        if existing_arch and existing_arch != arch:
            raise HaPxeError(
                f"Client {serial} architecture changed from {existing_arch} to {arch}; set rebuild: true before restarting the add-on"
            )

    if not (root_dir / "etc" / "os-release").exists() or not (boot_dir / "cmdline.txt").exists():
        _log_stage(context, "info", serial, "image", "started", "Selecting, downloading, and unpacking a Raspberry Pi OS image")
        image_url = latest_image_url(context, arch)
        context.logger.info(f"Selected Raspberry Pi OS image {image_url}")
        image_path = download_image(context, image_url)
        context.logger.info(f"Populating exports for {serial} from {image_path}")
        populate_from_image(context, image_path, boot_dir, root_dir)
        write_client_state(state_file, model, arch, image_url)
        _log_stage(context, "info", serial, "image", "completed", f"Exported boot and root filesystems from {Path(image_url).name}")
    else:
        _log_stage(context, "info", serial, "image", "skipped", "Reusing existing exported boot/root trees")

    _log_stage(
        context,
        "info",
        serial,
        "boot-config",
        "started",
        "Writing kernel command line, main config.txt entries, fstab mounts, and swap policy for network boot",
    )
    _rewrite_cmdline(context, boot_dir, server_ip, root_dir)
    _rewrite_boot_config(context, boot_dir, client)
    _rewrite_fstab(root_dir, server_ip, boot_dir)
    _disable_swap_for_network_root(root_dir)
    _log_stage(
        context,
        "info",
        serial,
        "boot-config",
        "completed",
        "PXE boot configuration updated, managed main config.txt entries applied, and swap disabled for network-root clients",
    )

    _log_stage(context, "info", serial, "bootstrap", "started", "Installing first-boot and container-sync bootstrap assets")
    _disable_stock_firstboot_services(root_dir)
    _write_bootstrap_files(context, root_dir, serial, hostname, server_ip, specs_to_json(containers))
    _log_stage(context, "info", serial, "bootstrap", "completed", "Bootstrap scripts, services, and transport settings installed")

    _log_stage(context, "info", serial, "nfs", "started", "Registering per-client NFS exports")
    append_exports(context, boot_dir, root_dir)
    _log_stage(context, "info", serial, "nfs", "completed", "Per-client NFS exports registered")

    _log_stage(context, "info", serial, "tftp", "started", "Publishing shared firmware and binding TFTP trees")
    publish_root_tftp_firmware(context, boot_dir, serial, model)
    bind_tftp_tree(context, boot_dir, serial, short_serial)
    _log_stage(context, "info", serial, "tftp", "completed", f"TFTP trees are bound for {serial} and {short_serial}")

    _log_stage(context, "info", serial, "prepare", "completed", f"Prepared client {hostname} ({serial}) using {arch}")


def _rewrite_cmdline(context: AddonContext, boot_dir: Path, server_ip: str, root_export: Path) -> None:
    existing = " ".join((boot_dir / "cmdline.txt").read_text(encoding="utf-8").split())
    cleaned_tokens: list[str] = []
    for token in existing.split():
        if token.startswith(CMDLINE_DROP_PREFIXES) or token in CMDLINE_DROP_TOKENS:
            continue
        if token not in cleaned_tokens:
            cleaned_tokens.append(token)

    context.logger.debug(f"Original cmdline for {boot_dir}: {existing}")
    prefix = f"{' '.join(cleaned_tokens)} " if cleaned_tokens else ""
    new_cmdline = (
        f"{prefix}root=/dev/nfs rootfstype=nfs "
        f"nfsroot={server_ip}:{root_export},vers=3,tcp,nolock rw ip=dhcp rootwait\n"
    )
    atomic_write(boot_dir / "cmdline.txt", new_cmdline)
    context.logger.debug(f"Rewritten cmdline for {boot_dir}: {new_cmdline.strip()}")


def _rewrite_boot_config(context: AddonContext, boot_dir: Path, client: dict[str, object]) -> None:
    config_path = boot_dir / "config.txt"
    managed_lines = _merge_boot_config_lines(
        str(context.config.get("boot_config_lines", "") or ""),
        str(client.get("boot_config_lines", "") or ""),
    )

    if not config_path.exists() and not managed_lines:
        return

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    rendered = _render_boot_config(existing, managed_lines)
    atomic_write(config_path, rendered)
    context.logger.debug(f"Rewritten config.txt for {boot_dir} with {len(managed_lines)} managed line(s)")


def _merge_boot_config_lines(*raw_values: str) -> list[str]:
    merged: list[str] = []
    for raw_value in raw_values:
        for line in raw_value.splitlines():
            normalized = line.strip()
            if not normalized or normalized in merged:
                continue
            merged.append(normalized)
    return merged


def _render_boot_config(existing: str, managed_lines: list[str]) -> str:
    output_lines: list[str] = []
    in_managed_block = False
    just_ended_managed_block = False

    for raw_line in existing.splitlines():
        stripped = raw_line.strip()
        if stripped == BOOT_CONFIG_MANAGED_START:
            in_managed_block = True
            continue
        if stripped == BOOT_CONFIG_MANAGED_END:
            in_managed_block = False
            just_ended_managed_block = True
            continue
        if not in_managed_block:
            if just_ended_managed_block and not raw_line.strip() and output_lines and not output_lines[-1].strip():
                just_ended_managed_block = False
                continue
            just_ended_managed_block = False
            output_lines.append(raw_line)

    while output_lines and not output_lines[-1].strip():
        output_lines.pop()

    if managed_lines:
        if output_lines:
            output_lines.append("")
        output_lines.extend(
            (
                BOOT_CONFIG_MANAGED_START,
                BOOT_CONFIG_RESET_SECTION,
                *managed_lines,
                BOOT_CONFIG_RESET_SECTION,
                BOOT_CONFIG_MANAGED_END,
            )
        )

    if not output_lines:
        return ""
    return "\n".join(output_lines) + "\n"


def _rewrite_fstab(root_dir: Path, server_ip: str, boot_export: Path) -> None:
    fstab_path = root_dir / "etc" / "fstab"
    output_lines: list[str] = []
    for raw_line in fstab_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            output_lines.append(raw_line)
            continue
        fields = raw_line.split()
        if len(fields) < 3:
            output_lines.append(raw_line)
            continue
        mount_point = fields[1]
        fs_type = fields[2]
        if mount_point in {"/", "/boot", "/boot/firmware"} or fs_type == "swap":
            continue
        output_lines.append(raw_line)
    output_lines.append(f"{server_ip}:{boot_export} /boot/firmware nfs defaults,vers=3,tcp,nolock,_netdev 0 0")
    atomic_write(fstab_path, "\n".join(output_lines) + "\n")


def _disable_swap_for_network_root(root_dir: Path) -> None:
    ensure_directory(root_dir / "etc" / "rpi" / "swap.conf.d")
    ensure_directory(root_dir / "etc" / "systemd" / "system")
    atomic_write(root_dir / "etc" / "rpi" / "swap.conf.d" / "90-ha-pxe-no-swap.conf", "[Main]\nMechanism=none\n")
    replace_symlink(root_dir / "etc" / "systemd" / "system" / "dphys-swapfile.service", "/dev/null")
    (root_dir / "var" / "swap").unlink(missing_ok=True)


def _disable_stock_firstboot_services(root_dir: Path) -> None:
    ensure_directory(root_dir / "etc" / "systemd" / "system")
    ensure_directory(root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants")
    replace_symlink(root_dir / "etc" / "systemd" / "system" / "userconfig.service", "/dev/null")
    replace_symlink(root_dir / "etc" / "systemd" / "system" / "systemd-firstboot.service", "/dev/null")
    (root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants" / "userconfig.service").unlink(missing_ok=True)
    (root_dir / "etc" / "ssh" / "sshd_config.d" / "rename_user.conf").unlink(missing_ok=True)
    banner_path = root_dir / "usr" / "share" / "userconf-pi" / "sshd_banner"
    if banner_path.exists():
        atomic_write(banner_path, "")


def _write_bootstrap_files(
    context: AddonContext,
    root_dir: Path,
    serial: str,
    hostname: str,
    server_ip: str,
    containers_json: str,
) -> None:
    username = str(context.config.get("default_username", "pi") or "pi")
    password = str(context.config.get("default_password", "") or "")
    keys = str(context.config.get("ssh_authorized_keys", "") or "")
    timezone = str(context.config.get("default_timezone", "") or "")
    keyboard_layout = str(context.config.get("default_keyboard_layout", "") or "")
    locale = str(context.config.get("default_locale", "") or "")
    password_hash = capture(["openssl", "passwd", "-6", password]) if password else ""

    ensure_directory(root_dir / "etc" / "ha-pxe")
    ensure_directory(root_dir / "usr" / "local" / "lib" / "ha-pxe")
    ensure_directory(root_dir / "usr" / "local" / "sbin")
    ensure_directory(root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants")
    ensure_directory(root_dir / "etc" / "systemd" / "system" / "timers.target.wants")
    ensure_directory(root_dir / "var" / "lib" / "ha-pxe")

    templates = context.paths.templates_dir
    copy_file(templates / "ha-pxe-firstboot.service", root_dir / "etc" / "systemd" / "system" / "ha-pxe-firstboot.service", 0o644)
    copy_file(templates / "ha-pxe-early-log.service", root_dir / "etc" / "systemd" / "system" / "ha-pxe-early-log.service", 0o644)
    copy_file(templates / "ha-pxe-container-sync.service", root_dir / "etc" / "systemd" / "system" / "ha-pxe-container-sync.service", 0o644)
    copy_file(templates / "ha-pxe-container-sync.timer", root_dir / "etc" / "systemd" / "system" / "ha-pxe-container-sync.timer", 0o644)
    copy_file(templates / "ha-pxe-early-log.py", root_dir / "usr" / "local" / "sbin" / "ha-pxe-early-log", 0o755)
    copy_file(templates / "ha-pxe-firstboot.py", root_dir / "usr" / "local" / "sbin" / "ha-pxe-firstboot", 0o755)
    copy_file(templates / "ha-pxe-container-sync.py", root_dir / "usr" / "local" / "sbin" / "ha-pxe-container-sync", 0o755)
    copy_tree(context.paths.package_dir, root_dir / "usr" / "local" / "lib" / "ha-pxe" / "ha_pxe")

    replace_symlink(root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants" / "ha-pxe-early-log.service", "../ha-pxe-early-log.service")
    replace_symlink(root_dir / "etc" / "systemd" / "system" / "timers.target.wants" / "ha-pxe-container-sync.timer", "../ha-pxe-container-sync.timer")

    bootstrap_values = {
        "PXE_USERNAME": username,
        "PXE_PASSWORD_HASH": password_hash,
        "PXE_HOSTNAME": hostname,
        "PXE_SERIAL": serial,
        "PXE_EXTRA_GROUPS": ",".join(DEFAULT_GROUPS),
        "PXE_DEFAULT_TIMEZONE": timezone,
        "PXE_DEFAULT_KEYBOARD_LAYOUT": keyboard_layout,
        "PXE_DEFAULT_LOCALE": locale,
        "PXE_LOG_HOST": server_ip,
        "PXE_LOG_PORT": str(context.paths.client_log_port),
        "PXE_LOG_PATH": context.paths.client_log_path,
    }
    atomic_write(root_dir / "etc" / "ha-pxe" / "bootstrap.env", format_env_file(bootstrap_values))

    authorized_keys_path = root_dir / "etc" / "ha-pxe" / "authorized_keys"
    atomic_write(authorized_keys_path, f"{keys}\n" if keys else "")
    atomic_write(root_dir / "etc" / "ha-pxe" / "containers.json", containers_json)
    atomic_write(root_dir / "etc" / "hostname", f"{hostname}\n")

    hosts_path = root_dir / "etc" / "hosts"
    if hosts_path.exists():
        lines = hosts_path.read_text(encoding="utf-8").splitlines()
        updated = False
        for index, line in enumerate(lines):
            if line.startswith("127.0.1.1") and line.split():
                lines[index] = f"127.0.1.1\t{hostname}"
                updated = True
                break
        if not updated:
            lines.append(f"127.0.1.1\t{hostname}")
        atomic_write(hosts_path, "\n".join(lines) + "\n")


def _log_stage(context: AddonContext, level: str, serial: str, stage: str, status: str, message: str) -> None:
    prefix = f"[client {serial}] stage={stage} status={status} {message}"
    if level == "error":
        context.logger.error(prefix)
    elif level == "warn":
        context.logger.warning(prefix)
    elif level == "debug":
        context.logger.debug(prefix)
    else:
        context.logger.info(prefix)

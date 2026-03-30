"""Bootstrap environment and shared client helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..envfile import parse_env_file
from ..fs_utils import copy_file, ensure_directory
from ..log_levels import normalize_log_level
from ..shell import command_exists, run


@dataclass
class ClientPaths:
    root: Path = field(default_factory=lambda: Path("/"))

    @property
    def bootstrap_env(self) -> Path:
        return self.root / "etc" / "ha-pxe" / "bootstrap.env"

    @property
    def authorized_keys(self) -> Path:
        return self.root / "etc" / "ha-pxe" / "authorized_keys"

    @property
    def containers_json(self) -> Path:
        return self.root / "etc" / "ha-pxe" / "containers.json"

    @property
    def firstboot_marker(self) -> Path:
        return self.root / "var" / "lib" / "ha-pxe" / "firstboot.done"

    @property
    def state_root(self) -> Path:
        return self.root / "var" / "lib" / "ha-pxe" / "containers"


@dataclass
class BootstrapConfig:
    username: str
    password_hash: str
    hostname: str
    serial: str
    extra_groups: str
    default_timezone: str
    default_keyboard_layout: str
    default_locale: str
    log_level: str
    log_host: str
    log_port: int
    log_path: str
    command_host: str
    command_port: int
    command_path: str

    @classmethod
    def load(cls, path: Path | None = None) -> "BootstrapConfig":
        values = parse_env_file(path or ClientPaths().bootstrap_env)
        return cls(
            username=values.get("PXE_USERNAME", "pi"),
            password_hash=values.get("PXE_PASSWORD_HASH", ""),
            hostname=values.get("PXE_HOSTNAME", ""),
            serial=values.get("PXE_SERIAL", ""),
            extra_groups=values.get("PXE_EXTRA_GROUPS", ""),
            default_timezone=values.get("PXE_DEFAULT_TIMEZONE", ""),
            default_keyboard_layout=values.get("PXE_DEFAULT_KEYBOARD_LAYOUT", ""),
            default_locale=values.get("PXE_DEFAULT_LOCALE", ""),
            log_level=normalize_log_level(values.get("PXE_LOG_LEVEL", "info")),
            log_host=values.get("PXE_LOG_HOST", ""),
            log_port=int(values.get("PXE_LOG_PORT", "0") or "0"),
            log_path=values.get("PXE_LOG_PATH", "/client-log"),
            command_host=values.get("PXE_COMMAND_HOST", values.get("PXE_LOG_HOST", "")),
            command_port=int(values.get("PXE_COMMAND_PORT", values.get("PXE_LOG_PORT", "0")) or "0"),
            command_path=values.get("PXE_COMMAND_PATH", "/client-command"),
        )


def configure_ssh_keys(config: BootstrapConfig, logger: object, paths: ClientPaths | None = None) -> None:
    client_paths = paths or ClientPaths()
    if not client_paths.authorized_keys.exists() or client_paths.authorized_keys.stat().st_size == 0:
        logger.info(f"No authorized SSH keys were supplied for {config.username}")
        return
    if run(["id", config.username], check=False).returncode != 0:
        logger.info(f"User {config.username} does not exist yet; deferring authorized SSH key installation")
        return

    ssh_dir = Path("/home") / config.username / ".ssh"
    ensure_directory(ssh_dir)
    run(["chown", f"{config.username}:{config.username}", str(ssh_dir)])
    run(["chmod", "700", str(ssh_dir)])
    copy_file(client_paths.authorized_keys, ssh_dir / "authorized_keys", 0o600)
    run(["chown", f"{config.username}:{config.username}", str(ssh_dir / "authorized_keys")])
    logger.info(f"Installed authorized SSH keys for {config.username}")


def clear_stock_ssh_banner(config: BootstrapConfig, logger: object) -> None:
    if command_exists("cancel-rename"):
        run(["cancel-rename", config.username], check=False)
    rename_config = Path("/etc/ssh/sshd_config.d/rename_user.conf")
    rename_config.unlink(missing_ok=True)
    banner_path = Path("/usr/share/userconf-pi/sshd_banner")
    if banner_path.exists():
        banner_path.write_text("", encoding="utf-8")
    logger.info("Cleared Raspberry Pi OS stock SSH rename prompts")

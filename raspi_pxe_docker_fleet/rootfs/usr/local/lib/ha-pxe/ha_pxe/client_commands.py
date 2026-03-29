"""File-backed command queue shared by the add-on and PXE clients."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .fs_utils import atomic_write, ensure_directory


DEFAULT_COMMAND_TTL_SECONDS = 300
_VALID_SERIAL_CHARS = set("0123456789abcdef")


def normalize_client_serial(value: str) -> str:
    serial = value.strip().lower().removeprefix("0x")
    if not serial or any(char not in _VALID_SERIAL_CHARS for char in serial):
        raise ValueError(f"Invalid client serial: {value}")
    return serial


def queue_client_command(
    commands_dir: Path,
    serial: str,
    name: str,
    *,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    now: float | None = None,
) -> None:
    normalized_serial = normalize_client_serial(serial)
    clean_name = name.strip().lower()
    if not clean_name:
        raise ValueError("Command name must not be empty")

    current_time = int(now if now is not None else time.time())
    expires_at = current_time + max(ttl_seconds, 0)
    existing = _load_valid_commands(_command_file(commands_dir, normalized_serial), current_time)
    remaining = [command for command in existing if str(command.get("name", "")) != clean_name]
    remaining.append({"name": clean_name, "expires_at": expires_at})
    _write_commands(commands_dir, normalized_serial, remaining)


def consume_client_commands(commands_dir: Path, serial: str, *, now: float | None = None) -> list[dict[str, str]]:
    normalized_serial = normalize_client_serial(serial)
    command_file = _command_file(commands_dir, normalized_serial)
    current_time = int(now if now is not None else time.time())
    commands = _load_valid_commands(command_file, current_time)
    command_file.unlink(missing_ok=True)
    return [{"name": str(command["name"])} for command in commands]


def queue_reconcile_command(
    commands_dir: Path,
    serial: str,
    *,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    now: float | None = None,
) -> None:
    queue_client_command(commands_dir, serial, "reconcile", ttl_seconds=ttl_seconds, now=now)


def _command_file(commands_dir: Path, serial: str) -> Path:
    return commands_dir / f"{serial}.json"


def _load_valid_commands(command_file: Path, current_time: int) -> list[dict[str, Any]]:
    if not command_file.exists():
        return []
    try:
        payload = json.loads(command_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        command_file.unlink(missing_ok=True)
        return []

    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, list):
        command_file.unlink(missing_ok=True)
        return []

    valid: list[dict[str, Any]] = []
    for raw_command in raw_commands:
        if not isinstance(raw_command, dict):
            continue
        name = str(raw_command.get("name", "")).strip().lower()
        expires_at = int(raw_command.get("expires_at", 0) or 0)
        if not name or expires_at <= current_time:
            continue
        valid.append({"name": name, "expires_at": expires_at})
    return valid


def _write_commands(commands_dir: Path, serial: str, commands: list[dict[str, Any]]) -> None:
    ensure_directory(commands_dir)
    atomic_write(
        _command_file(commands_dir, serial),
        json.dumps({"commands": commands}, indent=2) + "\n",
        0o600,
    )

"""Helpers for reading and writing simple shell-style env files."""

from __future__ import annotations

import shlex
from pathlib import Path


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, raw_value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            values[key] = ""
            continue
        try:
            parts = shlex.split(raw_value, posix=True)
            values[key] = parts[0] if parts else ""
        except ValueError:
            values[key] = raw_value
    return values


def format_env_file(values: dict[str, str]) -> str:
    lines = [f"{key}={shlex.quote(str(value))}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


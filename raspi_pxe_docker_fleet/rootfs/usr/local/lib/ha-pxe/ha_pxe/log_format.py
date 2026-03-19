"""Shared helpers for timestamped, color-aware log lines."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import TextIO


_RESET = "\033[0m"
_LEVEL_LABELS = {"error": "ERROR", "warn": "WARN", "debug": "DEBUG", "info": "INFO"}
_LEVEL_COLORS = {
    "error": "\033[31m",
    "warn": "\033[33m",
    "debug": "\033[36m",
    "info": "\033[32m",
}
_LEVEL_ICONS = {
    "error": "🔴",
    "warn": "🟡",
    "debug": "🔵",
    "info": "🟢",
}


def format_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return current.strftime("%Y-%m-%d %H:%M:%S%z")


def use_color(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") in {"1", "true", "TRUE", "yes", "YES"}:
        return True
    target = stream or sys.stderr
    return bool(getattr(target, "isatty", lambda: False)())


def format_log_line(
    level: str,
    message: str,
    *,
    name: str = "",
    color: bool | None = None,
    now: datetime | None = None,
) -> str:
    timestamp = format_timestamp(now)
    normalized_level = level if level in _LEVEL_LABELS else "info"
    level_label = f"[{_LEVEL_LABELS[normalized_level]}]"
    color_enabled = use_color() if color is None else color
    if color_enabled:
        level_label = f"{_LEVEL_COLORS[normalized_level]}{level_label}{_RESET}"
    else:
        level_label = f"{_LEVEL_ICONS[normalized_level]} {level_label}"

    parts = [f"[{timestamp}]", level_label]
    if name:
        parts.append(f"[{name}]")
    parts.append(message)
    return " ".join(parts)

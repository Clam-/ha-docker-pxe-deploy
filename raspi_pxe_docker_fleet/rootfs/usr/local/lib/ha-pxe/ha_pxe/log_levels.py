"""Shared log-level helpers for client and add-on logging."""

from __future__ import annotations


LOG_LEVELS = {"error": 0, "warn": 1, "info": 2, "debug": 3}


def normalize_log_level(value: str | None, default: str = "info") -> str:
    if value in LOG_LEVELS:
        return value
    return default if default in LOG_LEVELS else "info"


def should_log_level(requested: str, configured: str) -> bool:
    normalized_requested = normalize_log_level(requested)
    normalized_configured = normalize_log_level(configured)
    return LOG_LEVELS[normalized_requested] <= LOG_LEVELS[normalized_configured]

"""Small text helpers shared by add-on and client scripts."""

from __future__ import annotations

import json
import re
from typing import Any


TOKEN_RE = re.compile(r"[^a-z0-9_.-]+")
WHITESPACE_RE = re.compile(r"\s+")


def sanitize_token(value: str | None, default: str = "unknown") -> str:
    text = (value or default).strip().lower()
    text = TOKEN_RE.sub("-", text).strip("-")
    return text or default


def sanitize_message(value: str | None) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def slug(value: str | None, default: str = "container") -> str:
    return sanitize_token(value or default, default=default)


def stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


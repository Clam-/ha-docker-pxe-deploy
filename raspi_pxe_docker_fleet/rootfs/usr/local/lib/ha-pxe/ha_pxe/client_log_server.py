"""Small HTTP handler used behind socat for client log shipping."""

from __future__ import annotations

import sys

from .text import sanitize_message, sanitize_token


def main() -> int:
    request_line = sys.stdin.buffer.readline()
    if not request_line:
        return 0

    request_text = request_line.decode("utf-8", errors="replace").rstrip("\r\n")
    parts = request_text.split()
    if len(parts) != 3:
        _send_response("400 Bad Request")
        return 0

    method, path, protocol = parts
    if method != "POST":
        _send_response("405 Method Not Allowed")
        return 0
    if path != "/client-log":
        _send_response("404 Not Found")
        return 0
    if not protocol.startswith("HTTP/"):
        _send_response("400 Bad Request")
        return 0

    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text:
            break
        if ":" not in text:
            continue
        name, value = text.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    raw_length = sanitize_message(headers.get("content-length", "0"))
    if not raw_length.isdigit():
        _send_response("400 Bad Request")
        return 0

    content_length = int(raw_length)
    body = sys.stdin.buffer.read(content_length) if content_length > 0 else b""
    message = sanitize_message(body.decode("utf-8", errors="replace")) or "No message provided"

    source = sanitize_token(headers.get("x-ha-pxe-source"), "client")
    level = sanitize_token(headers.get("x-ha-pxe-level"), "info")
    stage = sanitize_token(headers.get("x-ha-pxe-stage"), "unknown")
    status = sanitize_token(headers.get("x-ha-pxe-status"), "message")
    hostname = sanitize_message(headers.get("x-ha-pxe-hostname", "")) or "unknown"
    serial = sanitize_message(headers.get("x-ha-pxe-serial", "")) or "unknown"
    exit_code = sanitize_message(headers.get("x-ha-pxe-exit-code", ""))

    _send_response("204 No Content")
    suffix = f" exit={exit_code}" if exit_code else ""
    print(
        f"[ha-pxe-client-transport] level={level} source={source} host={hostname} serial={serial} stage={stage} status={status}{suffix} {message}",
        file=sys.stderr,
        flush=True,
    )
    return 0


def _send_response(status: str) -> None:
    sys.stdout.buffer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.flush()

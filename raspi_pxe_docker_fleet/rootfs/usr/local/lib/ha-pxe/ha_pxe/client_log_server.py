"""Small HTTP server for client log shipping."""

from __future__ import annotations

import argparse
import http.server
import re
import sys
from collections.abc import Mapping

from .log_format import format_log_line
from .text import sanitize_message, sanitize_token


class ClientLogServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class ClientLogRequestHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ha-pxe-client-log"
    sys_version = ""

    def do_POST(self) -> None:  # noqa: N802
        expected_path = getattr(self.server, "log_path", "/client-log")
        if self.path != expected_path:
            self.send_error(404)
            return

        raw_length = sanitize_message(self.headers.get("Content-Length", "0"))
        if not raw_length.isdigit():
            self.send_error(400)
            return

        content_length = int(raw_length)
        body = self.rfile.read(content_length) if content_length > 0 else b""
        self._emit_log(body)

        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        self.send_error(405)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _emit_log(self, body: bytes) -> None:
        entry = format_log_entry(self.headers, body)
        if entry:
            print(entry, file=sys.stderr, flush=True)


def format_log_entry(headers: Mapping[str, str], body: bytes) -> str | None:
    message = sanitize_message(body.decode("utf-8", errors="replace")) or "No message provided"
    source = sanitize_token(headers.get("X-Ha-Pxe-Source"), "client")
    level = sanitize_token(headers.get("X-Ha-Pxe-Level"), "info")
    stage = sanitize_token(headers.get("X-Ha-Pxe-Stage"), "unknown")
    status = sanitize_token(headers.get("X-Ha-Pxe-Status"), "message")
    hostname = sanitize_message(headers.get("X-Ha-Pxe-Hostname", "")) or "unknown"
    serial = sanitize_message(headers.get("X-Ha-Pxe-Serial", "")) or "unknown"
    exit_code = sanitize_message(headers.get("X-Ha-Pxe-Exit-Code", ""))
    summary = summarize_log_entry(source, hostname, serial, level, stage, status, message, exit_code)
    if summary is None:
        return None
    return format_log_line(
        level,
        summary,
        name="ha-pxe-client-transport",
    )


def summarize_log_entry(
    source: str,
    hostname: str,
    serial: str,
    level: str,
    stage: str,
    status: str,
    message: str,
    exit_code: str = "",
) -> str | None:
    if source == "container-sync":
        summary = summarize_container_sync_entry(hostname, serial, stage, status, message, exit_code)
        if summary is not None or stage in {"preflight", "validate", "docker", "network", "cleanup", "reconcile", "summary"} or _RECONCILE_STAGE_RE.match(stage):
            return summary
    if source == "firstboot":
        summary = summarize_firstboot_entry(hostname, serial, stage, status, message, exit_code)
        if summary is not None or stage in {"preflight", "identity", "locale-defaults", "network-manager", "resolver", "time-sync", "packages", "access", "services", "finalize"}:
            return summary

    details = f"{stage} {status}: {message}"
    if exit_code:
        details = f"{details} (exit {exit_code})"
    return f"{_host_prefix(hostname, serial, include_serial=level in {'error', 'warn'})} {source}: {details}"


_PULLING_RE = re.compile(r"^Pulling (?P<image>\S+)$")
_PULLED_RE = re.compile(r"^Pulled (?P<image>\S+) successfully$")
_CREATE_RE = re.compile(r"^Container (?P<name>[^ ]+) does not exist yet; creating it$")
_UP_TO_DATE_RE = re.compile(r"^Container (?P<name>[^ ]+) is already up to date$")
_START_STOPPED_RE = re.compile(r"^Container (?P<name>[^ ]+) exists but is not running; attempting to start it$")
_RECREATING_RE = re.compile(r"^Recreating (?P<name>[^ ]+)$")
_STARTING_RE = re.compile(r"^Starting (?P<name>[^ ]+) from (?P<image>\S+)$")
_GENERATED_FILES_RE = re.compile(
    r"^Generated file content changed for (?P<name>[^;]+); updating bind-mounted files and restarting the existing container$"
)
_BUILDING_RE = re.compile(r"^Building (?P<image>\S+); detailed build output is being written to .+$")
_BUILT_RE = re.compile(r"^Built (?P<image>\S+) with updated fingerprint (?P<fingerprint>[a-f0-9]+)$")
_RECONCILE_STAGE_RE = re.compile(r"^reconcile-(?P<container>[a-z0-9_.-]+)$")
_STATE_DIR_RE = re.compile(r"^State directory for (?P<name>.+) is .+$")
_MATERIALIZING_RE = re.compile(r"^Materializing generated container files into .+$")
_MATERIALIZED_RE = re.compile(r"^Materialized \d+ generated file mount\(s\)$")
_GENERATED_MOUNT_RE = re.compile(r"^Prepared generated file mount .+$")


def summarize_container_sync_entry(
    hostname: str,
    serial: str,
    stage: str,
    status: str,
    message: str,
    exit_code: str = "",
) -> str | None:
    prefix = _host_prefix(hostname, serial)
    error_prefix = _host_prefix(hostname, serial, include_serial=True)

    if status in {"failed", "error", "warning"}:
        detail = message
        if exit_code:
            detail = f"{detail} (exit {exit_code})"
        return f"{error_prefix} container sync {status}: {detail}"

    if stage == "preflight" and status == "started":
        return f"{prefix} Reconciliation run started"
    if stage == "preflight" and status == "skipped":
        return f"{prefix} Reconciliation skipped: {message}"
    if stage == "validate" and status == "completed":
        return f"{prefix} {message}"
    if stage == "summary" and status == "completed":
        return f"{prefix} Reconciliation run completed successfully"
    if stage == "summary" and status == "started":
        return None

    if stage in {"docker", "network", "cleanup", "reconcile"}:
        return summarize_container_sync_stage(hostname, serial, stage, status, message)

    if _RECONCILE_STAGE_RE.match(stage):
        return summarize_container_sync_container_event(prefix, message)

    return None


def summarize_container_sync_stage(
    hostname: str,
    serial: str,
    stage: str,
    status: str,
    message: str,
) -> str | None:
    prefix = _host_prefix(hostname, serial)
    if stage == "network" and "Created managed Docker bridge network" in message:
        return f"{prefix} {message}"
    if stage == "network" and (
        "Ensuring the managed Docker bridge network is available" in message
        or "Managed Docker bridge network" in message
    ):
        return None
    if stage == "cleanup" and message.startswith("Removing stale container "):
        return f"{prefix} {message}"
    if stage == "cleanup" and message.startswith("Removing stale state directory "):
        return f"{prefix} {message}"
    if stage == "cleanup":
        return None
    if stage == "docker" and (
        "Ensuring the Docker daemon is available" in message
        or "Starting docker.service before reconciling managed containers" in message
        or "docker.service is running" in message
        or "Docker is ready and state directories exist" in message
    ):
        return None
    if stage == "reconcile" and (
        "Reconciling each desired managed container" in message
        or "All managed containers were reconciled successfully" in message
    ):
        return None
    return None


def summarize_container_sync_container_event(prefix: str, message: str) -> str | None:
    if (
        _STATE_DIR_RE.match(message)
        or _MATERIALIZING_RE.match(message)
        or _MATERIALIZED_RE.match(message)
        or _GENERATED_MOUNT_RE.match(message)
        or message.startswith("Restarting ")
    ):
        return None

    match = _PULLING_RE.match(message)
    if match:
        return f"{prefix} Pulling {match.group('image')}"

    match = _PULLED_RE.match(message)
    if match:
        return f"{prefix} Pulled {match.group('image')}"

    match = _CREATE_RE.match(message)
    if match:
        return f"{prefix} Creating {match.group('name')}"

    match = _RECREATING_RE.match(message)
    if match:
        return f"{prefix} Recreating {match.group('name')}"

    match = _STARTING_RE.match(message)
    if match:
        return f"{prefix} Starting {match.group('name')} from {match.group('image')}"

    match = _UP_TO_DATE_RE.match(message)
    if match:
        return f"{prefix} {match.group('name')} already up to date"

    match = _START_STOPPED_RE.match(message)
    if match:
        return f"{prefix} Starting stopped container {match.group('name')}"

    match = _GENERATED_FILES_RE.match(message)
    if match:
        return f"{prefix} Restarting {match.group('name')} after generated file updates"

    match = _BUILDING_RE.match(message)
    if match:
        return f"{prefix} Building {match.group('image')}"

    match = _BUILT_RE.match(message)
    if match:
        return f"{prefix} Built {match.group('image')}"

    if message.startswith("Created container "):
        return f"{prefix} {message}"
    if message.startswith("Recreated container "):
        return f"{prefix} {message}"
    if message.startswith("Restarted container "):
        return f"{prefix} {message}"

    return f"{prefix} {message}"


def summarize_firstboot_entry(
    hostname: str,
    serial: str,
    stage: str,
    status: str,
    message: str,
    exit_code: str = "",
) -> str | None:
    prefix = _host_prefix(hostname, serial)
    error_prefix = _host_prefix(hostname, serial, include_serial=True)

    if status in {"failed", "error", "warning"}:
        detail = message
        if exit_code:
            detail = f"{detail} (exit {exit_code})"
        return f"{error_prefix} first boot {status}: {detail}"

    if stage == "preflight" and status == "started":
        return f"{prefix} First-boot provisioning started"
    if stage == "preflight" and status == "skipped":
        return f"{prefix} First-boot skipped: {message}"
    if stage == "finalize" and status == "completed":
        return f"{prefix} First-boot provisioning completed successfully"
    if status == "completed" and stage in {"packages", "services", "access"}:
        return f"{prefix} {message}"
    return None


def _host_prefix(hostname: str, serial: str, *, include_serial: bool = False) -> str:
    if include_serial and serial and serial != "unknown":
        return f"{hostname} ({serial}):"
    return f"{hostname}:"


def serve(host: str, port: int, path: str) -> int:
    with ClientLogServer((host, port), ClientLogRequestHandler) as server:
        server.log_path = path
        server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ha-pxe client log transport server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--path", default="/client-log")
    args = parser.parse_args(argv)
    return serve(args.host, args.port, args.path)

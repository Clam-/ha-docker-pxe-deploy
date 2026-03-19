"""Small HTTP server for client log shipping."""

from __future__ import annotations

import argparse
import http.server
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
        print(format_log_entry(self.headers, body), file=sys.stderr, flush=True)


def format_log_entry(headers: Mapping[str, str], body: bytes) -> str:
    message = sanitize_message(body.decode("utf-8", errors="replace")) or "No message provided"
    source = sanitize_token(headers.get("X-Ha-Pxe-Source"), "client")
    level = sanitize_token(headers.get("X-Ha-Pxe-Level"), "info")
    stage = sanitize_token(headers.get("X-Ha-Pxe-Stage"), "unknown")
    status = sanitize_token(headers.get("X-Ha-Pxe-Status"), "message")
    hostname = sanitize_message(headers.get("X-Ha-Pxe-Hostname", "")) or "unknown"
    serial = sanitize_message(headers.get("X-Ha-Pxe-Serial", "")) or "unknown"
    exit_code = sanitize_message(headers.get("X-Ha-Pxe-Exit-Code", ""))
    suffix = f" exit={exit_code}" if exit_code else ""
    return format_log_line(
        level,
        (
            f"source={source} host={hostname} serial={serial} "
            f"stage={stage} status={status}{suffix} {message}"
        ),
        name="ha-pxe-client-transport",
    )


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

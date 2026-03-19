"""Client logging with local stderr output and remote log shipping."""

from __future__ import annotations

import http.client
import sys
import traceback
from dataclasses import dataclass

from ..log_format import format_log_line
from ..text import sanitize_message, sanitize_token
from .bootstrap import BootstrapConfig


@dataclass
class ClientLogger:
    config: BootstrapConfig
    prefix: str
    source: str
    current_stage: str = "startup"
    remote_failure_reported: bool = False

    def emit_local(self, level: str, stage: str, status: str, message: str) -> None:
        clean = sanitize_message(message)
        print(
            format_log_line(level, f"stage={stage} status={status} {clean}", name=self.prefix),
            file=sys.stderr,
            flush=True,
        )

    def emit_remote(self, level: str, stage: str, status: str, message: str, exit_code: str = "") -> bool:
        if not (self.config.log_host and self.config.log_port and self.config.log_path):
            return True

        body = sanitize_message(message)
        headers = {
            "Connection": "close",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Ha-Pxe-Source": self.source,
            "X-Ha-Pxe-Level": level,
            "X-Ha-Pxe-Stage": stage,
            "X-Ha-Pxe-Status": status,
            "X-Ha-Pxe-Hostname": self.config.hostname,
            "X-Ha-Pxe-Serial": self.config.serial,
        }
        if exit_code:
            headers["X-Ha-Pxe-Exit-Code"] = exit_code

        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(self.config.log_host, self.config.log_port, timeout=3)
            connection.request("POST", self.config.log_path, body=body.encode("utf-8"), headers=headers)
            response = connection.getresponse()
            response.read()
            if response.status in {200, 204}:
                self.remote_failure_reported = False
                return True
        except (OSError, http.client.HTTPException):
            pass
        finally:
            if connection is not None:
                connection.close()

        if not self.remote_failure_reported:
            print(
                format_log_line(
                    "warn",
                    (
                        "stage=transport status=degraded "
                        f"Unable to reach add-on log transport at {self.config.log_host}:{self.config.log_port}{self.config.log_path}"
                    ),
                    name=self.prefix,
                ),
                file=sys.stderr,
                flush=True,
            )
            self.remote_failure_reported = True
        return False

    def log(self, level: str, stage: str, status: str, message: str, exit_code: str = "") -> None:
        clean_level = sanitize_token(level, "info")
        clean_stage = sanitize_token(stage or self.current_stage, "unknown")
        clean_status = sanitize_token(status, "message")
        clean_message = sanitize_message(message) or "No details provided"
        self.current_stage = clean_stage
        self.emit_local(clean_level, clean_stage, clean_status, clean_message)
        self.emit_remote(clean_level, clean_stage, clean_status, clean_message, exit_code)

    def info(self, message: str) -> None:
        self.log("info", self.current_stage, "in_progress", message)

    def warning(self, message: str) -> None:
        self.log("warn", self.current_stage, "warning", message)

    def error(self, message: str, exit_code: str = "") -> None:
        self.log("error", self.current_stage, "error", message, exit_code)

    def stage_start(self, stage: str, message: str) -> None:
        self.current_stage = sanitize_token(stage, "unknown")
        self.log("info", self.current_stage, "started", message)

    def stage_complete(self, stage: str, message: str) -> None:
        clean_stage = sanitize_token(stage or self.current_stage, "unknown")
        self.current_stage = clean_stage
        self.log("info", clean_stage, "completed", message)

    def stage_skip(self, stage: str, message: str) -> None:
        clean_stage = sanitize_token(stage or self.current_stage, "unknown")
        self.current_stage = clean_stage
        self.log("info", clean_stage, "skipped", message)

    def stage_fail(self, stage: str, message: str, exit_code: str = "") -> None:
        clean_stage = sanitize_token(stage or self.current_stage, "unknown")
        self.current_stage = clean_stage
        self.log("error", clean_stage, "failed", message, exit_code)

    def fail_exception(self, exc: Exception) -> None:
        trace = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        if trace:
            last = trace[-1]
            message = f"Script failed at line {last.lineno}: {exc}"
        else:
            message = str(exc)
        exit_code = str(getattr(exc, "returncode", 1))
        self.stage_fail(self.current_stage, message, exit_code)

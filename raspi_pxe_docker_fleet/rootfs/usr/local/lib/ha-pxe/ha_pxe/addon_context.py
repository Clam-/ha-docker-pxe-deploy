"""Add-on configuration, logging, and Supervisor access."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .log_format import format_log_line


LOG_LEVELS = {"error": 0, "warn": 1, "info": 2, "debug": 3}


@dataclass
class AddonPaths:
    root: Path = field(default_factory=lambda: Path(os.environ.get("HA_PXE_ROOT", "/data")))
    library_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    os_page: str = "https://www.raspberrypi.com/software/operating-systems/"
    client_log_port: int = 8099
    client_log_path: str = "/client-log"

    @property
    def options_file(self) -> Path:
        return self.root / "options.json"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache" / "images"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def state_dir(self) -> Path:
        return self.root / "state" / "clients"

    @property
    def tftp_dir(self) -> Path:
        return self.root / "tftp"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def exports_file(self) -> Path:
        return self.runtime_dir / "exports"

    @property
    def dhcp_hints_file(self) -> Path:
        return self.runtime_dir / "dhcp-example.txt"

    @property
    def templates_dir(self) -> Path:
        return self.library_dir / "templates"

    @property
    def package_dir(self) -> Path:
        return self.library_dir / "ha_pxe"


class AddonLogger:
    def __init__(self, level: str = "info") -> None:
        self.level = level if level in LOG_LEVELS else "info"

    def configure(self, level: str) -> None:
        if level in LOG_LEVELS:
            self.level = level
        else:
            self.level = "info"
            self.warning(f"Unsupported log_level '{level}', defaulting to info")

    def should_log(self, level: str) -> bool:
        requested = LOG_LEVELS.get(level, LOG_LEVELS["info"])
        configured = LOG_LEVELS.get(self.level, LOG_LEVELS["info"])
        return requested <= configured

    def _emit(self, level: str, message: str) -> None:
        if not self.should_log(level):
            return
        print(format_log_line(level, message), file=sys.stderr, flush=True)

    def info(self, message: str) -> None:
        self._emit("info", message)

    def warning(self, message: str) -> None:
        self._emit("warn", message)

    def error(self, message: str) -> None:
        self._emit("error", message)

    def debug(self, message: str) -> None:
        self._emit("debug", message)


@dataclass
class AddonContext:
    paths: AddonPaths = field(default_factory=AddonPaths)
    logger: AddonLogger = field(default_factory=AddonLogger)
    background_processes: list[Any] = field(default_factory=list)
    _config_cache: dict[str, Any] | None = None
    _mqtt_status_logged: bool = False

    @property
    def config(self) -> dict[str, Any]:
        if self._config_cache is None:
            self._config_cache = json.loads(self.paths.options_file.read_text(encoding="utf-8"))
        return self._config_cache

    def configure_logging(self) -> None:
        self.logger.configure(str(self.config.get("log_level", "info") or "info"))
        self.logger.info(f"Configured log level: {self.logger.level}")

    def supervisor_api(self, endpoint: str) -> dict[str, Any] | None:
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return None
        request = urllib.request.Request(
            f"http://supervisor{endpoint}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    def host_hostname(self) -> str:
        response = self.supervisor_api("/host/info")
        if not response:
            return ""
        data = response.get("data")
        if not isinstance(data, dict):
            return ""
        hostname = data.get("hostname")
        return str(hostname) if hostname else ""

    def mqtt_host_suffix(self) -> str:
        return str(self.config.get("mqtt_host_suffix", "") or "").strip().strip(".")

    def qualified_host_hostname(self) -> str:
        hostname = self.host_hostname().strip().rstrip(".")
        if not hostname or "." in hostname:
            return hostname

        suffix = self.mqtt_host_suffix()
        if not suffix:
            return hostname
        return f"{hostname}.{suffix}"

    def service_info(self, service: str) -> dict[str, Any]:
        response = self.supervisor_api(f"/services/{service}")
        if not response:
            return {}
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    def mqtt_env_defaults(self) -> dict[str, str]:
        info = self.service_info("mqtt")
        host = self.qualified_host_hostname()
        port = str(info.get("port", "") or "")
        username = str(info.get("username", "") or "")
        password = str(info.get("password", "") or "")
        mqtt_available = bool(info)

        if not self._mqtt_status_logged:
            if not mqtt_available:
                self.logger.warning(
                    "MQTT service is unavailable; MQTT_PORT, MQTT_USERNAME, and MQTT_PASSWORD will not be injected into child containers"
                )
            else:
                if not port:
                    self.logger.warning("MQTT service did not provide a port; MQTT_PORT will not be injected into child containers")
                if not username:
                    self.logger.warning(
                        "MQTT service did not provide a username; MQTT_USERNAME will not be injected into child containers"
                    )
                if not password:
                    self.logger.warning(
                        "MQTT service did not provide a password; MQTT_PASSWORD will not be injected into child containers"
                    )
            if not host:
                self.logger.warning(
                    "Supervisor host hostname is unavailable; MQTT_HOST and MQTT_BROKER will not be injected into child containers"
                )
            self._mqtt_status_logged = True

        env: dict[str, str] = {}
        if host:
            env["MQTT_BROKER"] = host
            env["MQTT_HOST"] = host
        if port:
            env["MQTT_PORT"] = port
        if username:
            env["MQTT_USERNAME"] = username
        if password:
            env["MQTT_PASSWORD"] = password
        return env

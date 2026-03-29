"""Long-running client poller for add-on initiated commands."""

from __future__ import annotations

import http.client
import json
import time

from ..text import sanitize_token
from ..shell import run
from .bootstrap import BootstrapConfig
from .logging import ClientLogger


DEFAULT_POLL_INTERVAL_SECONDS = 15


def main() -> int:
    config = BootstrapConfig.load()
    logger = ClientLogger(config, prefix="ha-pxe-command-listener", source="command-listener")

    try:
        logger.stage_start("listener", f"Starting remote command listener for {config.hostname} ({config.serial})")
        run_forever(config, logger)
    except KeyboardInterrupt:
        logger.stage_skip("listener", "Remote command listener stopped")
        return 130
    return 0


def run_forever(
    config: BootstrapConfig,
    logger: ClientLogger,
    *,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    transport_degraded = False

    while True:
        try:
            commands = fetch_commands(config)
            if transport_degraded:
                logger.current_stage = "poll"
                logger.info("Command transport connectivity restored")
                transport_degraded = False
            for command in commands:
                execute_command(command, logger)
        except Exception as exc:  # noqa: BLE001
            if not transport_degraded:
                logger.current_stage = "poll"
                logger.warning(f"Command transport unavailable: {exc}")
                transport_degraded = True
        time.sleep(max(poll_interval_seconds, 1))


def fetch_commands(config: BootstrapConfig, *, timeout_seconds: int = 5) -> list[dict[str, str]]:
    if not (config.command_host and config.command_port and config.command_path):
        return []

    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(config.command_host, config.command_port, timeout=timeout_seconds)
        connection.request(
            "GET",
            config.command_path,
            headers={
                "Connection": "close",
                "X-Ha-Pxe-Hostname": config.hostname,
                "X-Ha-Pxe-Serial": config.serial,
            },
        )
        response = connection.getresponse()
        body = response.read()
    finally:
        if connection is not None:
            connection.close()

    if response.status in {204, 404}:
        return []
    if response.status != 200:
        raise RuntimeError(f"Add-on command transport returned HTTP {response.status}")

    payload = json.loads(body.decode("utf-8") or "{}")
    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, list):
        return []

    commands: list[dict[str, str]] = []
    for raw_command in raw_commands:
        if not isinstance(raw_command, dict):
            continue
        name = sanitize_token(str(raw_command.get("name", "")), "")
        if not name:
            continue
        commands.append({"name": name})
    return commands


def execute_command(command: dict[str, str], logger: ClientLogger) -> None:
    name = sanitize_token(command.get("name", ""), "")
    logger.current_stage = "command"
    if name == "reconcile":
        logger.info("Received reconcile command from the add-on")
        completed = run(["systemctl", "start", "ha-pxe-container-sync.service"], check=False)
        if completed.returncode == 0:
            logger.info("Triggered ha-pxe-container-sync.service from the add-on command")
            return
        logger.warning(
            f"Failed to start ha-pxe-container-sync.service from the add-on command (exit {completed.returncode})"
        )
        return

    logger.warning(f"Ignoring unsupported add-on command {name}")

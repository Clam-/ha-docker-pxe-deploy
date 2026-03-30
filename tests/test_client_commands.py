from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.client.bootstrap import BootstrapConfig
from ha_pxe.client.command_listener import execute_command, fetch_commands
from ha_pxe.client_commands import consume_client_commands, queue_reconcile_command


class QueueClientCommandTests(unittest.TestCase):
    def test_queue_reconcile_command_is_delivered_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            commands_dir = Path(temp_dir_name)

            queue_reconcile_command(commands_dir, "0xCDC843D7", ttl_seconds=300, now=100)

            self.assertEqual(consume_client_commands(commands_dir, "cdc843d7", now=200), [{"name": "reconcile"}])
            self.assertEqual(consume_client_commands(commands_dir, "cdc843d7", now=200), [])

    def test_queue_reconcile_command_expires_for_late_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            commands_dir = Path(temp_dir_name)

            queue_reconcile_command(commands_dir, "cdc843d7", ttl_seconds=10, now=100)

            self.assertEqual(consume_client_commands(commands_dir, "cdc843d7", now=111), [])


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConnection:
    last_instance: "_FakeConnection | None" = None
    next_response: _FakeResponse = _FakeResponse(204, b"")

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False
        _FakeConnection.last_instance = self

    def request(self, method: str, url: str, headers: dict[str, str]) -> None:
        self.requests.append((method, url, headers))

    def getresponse(self) -> _FakeResponse:
        return self.next_response

    def close(self) -> None:
        self.closed = True


class FetchCommandsTests(unittest.TestCase):
    def test_fetch_commands_requests_pending_commands_for_client_serial(self) -> None:
        config = BootstrapConfig(
            username="pi",
            password_hash="",
            hostname="janky",
            serial="cdc843d7",
            extra_groups="",
            default_timezone="",
            default_keyboard_layout="",
            default_locale="",
            log_level="info",
            log_host="",
            log_port=0,
            log_path="",
            command_host="addon.local",
            command_port=8099,
            command_path="/client-command",
        )
        _FakeConnection.next_response = _FakeResponse(200, json.dumps({"commands": [{"name": "reconcile"}]}).encode("utf-8"))

        with patch("ha_pxe.client.command_listener.http.client.HTTPConnection", _FakeConnection):
            commands = fetch_commands(config)

        self.assertEqual(commands, [{"name": "reconcile"}])
        instance = _FakeConnection.last_instance
        assert instance is not None
        self.assertEqual(instance.requests, [("GET", "/client-command", {"Connection": "close", "X-Ha-Pxe-Hostname": "janky", "X-Ha-Pxe-Serial": "cdc843d7"})])
        self.assertTrue(instance.closed)

    def test_fetch_commands_treats_missing_endpoint_as_noop(self) -> None:
        config = BootstrapConfig(
            username="pi",
            password_hash="",
            hostname="janky",
            serial="cdc843d7",
            extra_groups="",
            default_timezone="",
            default_keyboard_layout="",
            default_locale="",
            log_level="info",
            log_host="",
            log_port=0,
            log_path="",
            command_host="addon.local",
            command_port=8099,
            command_path="/client-command",
        )
        _FakeConnection.next_response = _FakeResponse(404, b"")

        with patch("ha_pxe.client.command_listener.http.client.HTTPConnection", _FakeConnection):
            commands = fetch_commands(config)

        self.assertEqual(commands, [])


class _FakeLogger:
    def __init__(self) -> None:
        self.current_stage = "listener"
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(f"info:{message}")

    def warning(self, message: str) -> None:
        self.messages.append(f"warn:{message}")


class ExecuteCommandTests(unittest.TestCase):
    def test_execute_command_starts_container_sync_service(self) -> None:
        logger = _FakeLogger()

        with patch(
            "ha_pxe.client.command_listener.run",
            return_value=CompletedProcess(["systemctl", "start", "ha-pxe-container-sync.service"], 0, "", ""),
        ) as run_mock:
            execute_command({"name": "reconcile"}, logger)

        run_mock.assert_called_once_with(["systemctl", "start", "ha-pxe-container-sync.service"], check=False)
        self.assertEqual(
            logger.messages,
            [
                "info:Received reconcile command from the add-on",
                "info:Triggered ha-pxe-container-sync.service from the add-on command",
            ],
        )

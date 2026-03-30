from __future__ import annotations

import io
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.client.bootstrap import BootstrapConfig
from ha_pxe.client.logging import ClientLogger
from ha_pxe.client_log_server import format_filtered_log_entry, format_log_entry


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_called = False

    def read(self) -> bytes:
        self.read_called = True
        return b""


class _FakeConnection:
    last_instance: "_FakeConnection | None" = None

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.response = _FakeResponse(204)
        self.closed = False
        _FakeConnection.last_instance = self

    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, url, body, headers))

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class ClientLoggerTests(unittest.TestCase):
    def test_emit_local_includes_timestamped_prefix(self) -> None:
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
            command_host="",
            command_port=0,
            command_path="",
        )
        logger = ClientLogger(config, prefix="ha-pxe-container-sync", source="container-sync")
        fake_stderr = io.StringIO()

        with patch("ha_pxe.client.logging.sys.stderr", fake_stderr):
            logger.emit_local("info", "reconcile", "started", "hello")

        self.assertRegex(
            fake_stderr.getvalue().strip(),
            re.compile(
                r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] "
                r"🟢 \[INFO\] \[ha-pxe-container-sync\] stage=reconcile status=started hello$"
            ),
        )

    def test_emit_remote_posts_with_http_connection_and_consumes_response(self) -> None:
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
            log_host="addon.local",
            log_port=8099,
            log_path="/client-log",
            command_host="addon.local",
            command_port=8099,
            command_path="/client-command",
        )
        logger = ClientLogger(config, prefix="ha-pxe-container-sync", source="container-sync")

        with patch("ha_pxe.client.logging.http.client.HTTPConnection", _FakeConnection):
            result = logger.emit_remote("info", "reconcile", "started", "hello", "7")

        self.assertTrue(result)
        instance = _FakeConnection.last_instance
        assert instance is not None
        self.assertEqual(instance.host, "addon.local")
        self.assertEqual(instance.port, 8099)
        self.assertEqual(len(instance.requests), 1)
        method, url, body, headers = instance.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "/client-log")
        self.assertEqual(body, b"hello")
        self.assertEqual(headers["X-Ha-Pxe-Exit-Code"], "7")
        self.assertTrue(instance.response.read_called)
        self.assertTrue(instance.closed)

    def test_log_suppresses_local_and_remote_messages_below_client_threshold(self) -> None:
        config = BootstrapConfig(
            username="pi",
            password_hash="",
            hostname="janky",
            serial="cdc843d7",
            extra_groups="",
            default_timezone="",
            default_keyboard_layout="",
            default_locale="",
            log_level="warn",
            log_host="addon.local",
            log_port=8099,
            log_path="/client-log",
            command_host="addon.local",
            command_port=8099,
            command_path="/client-command",
        )
        logger = ClientLogger(config, prefix="ha-pxe-container-sync", source="container-sync")
        fake_stderr = io.StringIO()
        _FakeConnection.last_instance = None

        with (
            patch("ha_pxe.client.logging.sys.stderr", fake_stderr),
            patch("ha_pxe.client.logging.http.client.HTTPConnection", _FakeConnection),
        ):
            logger.info("hello")

        self.assertEqual(fake_stderr.getvalue(), "")
        self.assertIsNone(_FakeConnection.last_instance)


class ClientLogRequestHandlerTests(unittest.TestCase):
    def test_format_log_entry_compacts_container_sync_run_start(self) -> None:
        entry = format_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "info",
                "X-Ha-Pxe-Stage": "preflight",
                "X-Ha-Pxe-Status": "started",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
            },
            b"Starting managed container reconciliation for janky (cdc843d7)",
        )

        assert entry is not None
        self.assertRegex(
            entry,
            re.compile(
                r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] "
                r"🟢 \[INFO\] \[ha-pxe-client-transport\] "
            ),
        )
        self.assertIn("[ha-pxe-client-transport]", entry)
        self.assertIn("janky: Reconciliation run started", entry)

    def test_format_log_entry_suppresses_low_signal_container_sync_noise(self) -> None:
        entry = format_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "info",
                "X-Ha-Pxe-Stage": "cleanup",
                "X-Ha-Pxe-Status": "in_progress",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
            },
            b"Managed container inventory before cleanup: rgpiod[key=abc,state=running]",
        )

        self.assertIsNone(entry)

    def test_format_log_entry_compacts_container_recreate_event(self) -> None:
        entry = format_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "info",
                "X-Ha-Pxe-Stage": "reconcile-rgpiod",
                "X-Ha-Pxe-Status": "completed",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
            },
            b"Recreated container rgpiod with updated image or spec",
        )

        assert entry is not None
        self.assertIn("janky: Recreated container rgpiod with updated image or spec", entry)

    def test_format_log_entry_keeps_failure_details_concise(self) -> None:
        entry = format_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "error",
                "X-Ha-Pxe-Stage": "reconcile-rgpiod",
                "X-Ha-Pxe-Status": "failed",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
                "X-Ha-Pxe-Exit-Code": "1",
            },
            b"Failed to reconcile container rgpiod: boom",
        )

        assert entry is not None
        self.assertIn("janky (cdc843d7): container sync failed: Failed to reconcile container rgpiod: boom (exit 1)", entry)

    def test_format_filtered_log_entry_respects_addon_threshold(self) -> None:
        entry = format_filtered_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "info",
                "X-Ha-Pxe-Stage": "preflight",
                "X-Ha-Pxe-Status": "started",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
            },
            b"Starting managed container reconciliation for janky (cdc843d7)",
            "warn",
        )

        self.assertIsNone(entry)

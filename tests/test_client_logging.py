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
from ha_pxe.client_log_server import format_log_entry


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
            log_host="",
            log_port=0,
            log_path="",
        )
        logger = ClientLogger(config, prefix="ha-pxe-container-sync", source="container-sync")
        fake_stderr = io.StringIO()

        with patch("ha_pxe.client.logging.sys.stderr", fake_stderr):
            logger.emit_local("info", "reconcile", "started", "hello")

        self.assertRegex(
            fake_stderr.getvalue().strip(),
            re.compile(
                r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] "
                r"\[INFO\] \[ha-pxe-container-sync\] stage=reconcile status=started hello$"
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
            log_host="addon.local",
            log_port=8099,
            log_path="/client-log",
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


class ClientLogRequestHandlerTests(unittest.TestCase):
    def test_format_log_entry_emits_transport_log(self) -> None:
        entry = format_log_entry(
            {
                "X-Ha-Pxe-Source": "container-sync",
                "X-Ha-Pxe-Level": "info",
                "X-Ha-Pxe-Stage": "reconcile",
                "X-Ha-Pxe-Status": "started",
                "X-Ha-Pxe-Hostname": "janky",
                "X-Ha-Pxe-Serial": "cdc843d7",
            },
            b"hello",
        )

        self.assertRegex(
            entry,
            re.compile(
                r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] "
                r"\[INFO\] \[ha-pxe-client-transport\] "
            ),
        )
        self.assertIn("[ha-pxe-client-transport]", entry)
        self.assertIn("source=container-sync", entry)
        self.assertIn("hello", entry)

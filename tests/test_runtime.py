from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext, AddonPaths
from ha_pxe.runtime import start_client_log_transport, start_tftp_server


class _RunningProcess:
    def poll(self) -> None:
        return None


class StartTftpServerTests(unittest.TestCase):
    def test_start_tftp_server_binds_dnsmasq_to_server_ip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            spawned_commands: list[list[str]] = []

            def fake_spawn(command: list[str]) -> _RunningProcess:
                spawned_commands.append(command)
                return _RunningProcess()

            with patch("ha_pxe.runtime.spawn", side_effect=fake_spawn):
                start_tftp_server(context, "192.0.2.10")

            self.assertEqual(len(spawned_commands), 1)
            self.assertIn("--listen-address=192.0.2.10", spawned_commands[0])


class StartClientLogTransportTests(unittest.TestCase):
    def test_start_client_log_transport_uses_python_server_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            spawned_commands: list[list[str]] = []

            def fake_spawn(command: list[str]) -> _RunningProcess:
                spawned_commands.append(command)
                return _RunningProcess()

            with patch("ha_pxe.runtime.spawn", side_effect=fake_spawn):
                start_client_log_transport(context)

            self.assertEqual(len(spawned_commands), 1)
            self.assertTrue(spawned_commands[0][0].endswith("client-log-server.py"))
            self.assertEqual(
                spawned_commands[0][1:],
                ["--host", "0.0.0.0", "--port", "8099", "--path", "/client-log"],
            )


if __name__ == "__main__":
    unittest.main()

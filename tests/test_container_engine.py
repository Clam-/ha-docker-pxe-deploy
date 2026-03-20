from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.client.container_engine import (
    MANAGED_DOCKER_NETWORK_NAME,
    container_name_for_spec,
    ensure_managed_network,
    run_container,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


class ContainerEngineTests(unittest.TestCase):
    def test_container_name_for_spec_defaults_to_spec_name(self) -> None:
        self.assertEqual(
            container_name_for_spec(
                {
                    "name": "rgpiod",
                    "source": {"type": "image", "ref": "docker.io/library/busybox:latest"},
                }
            ),
            "rgpiod",
        )

    def test_run_container_uses_managed_bridge_and_name_alias_by_default(self) -> None:
        logger = _FakeLogger()
        commands: list[list[str]] = []
        spec = {
            "name": "rgpiod",
            "container_name": "rgpiod",
            "image": "docker.io/library/busybox:latest",
            "restart": "unless-stopped",
            "network_mode": "",
            "privileged": False,
            "workdir": "",
            "env": {},
            "labels": {},
            "devices": [],
            "extra_hosts": [],
            "ports": [],
            "volumes": [],
            "command": [],
        }

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("ha_pxe.client.container_engine.run", side_effect=fake_run):
            run_container(spec, "key123", "rgpiod", "digest123", [], logger, "serial123")

        self.assertEqual(len(commands), 1)
        self.assertIn("--name", commands[0])
        self.assertIn("rgpiod", commands[0])
        self.assertIn(MANAGED_DOCKER_NETWORK_NAME, commands[0])
        self.assertIn("--network-alias", commands[0])

    def test_ensure_managed_network_creates_bridge_when_missing(self) -> None:
        logger = _FakeLogger()
        commands: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("ha_pxe.client.container_engine._capture_optional", return_value=""),
            patch("ha_pxe.client.container_engine.run", side_effect=fake_run),
        ):
            ensure_managed_network(logger, "serial123")

        self.assertEqual(len(commands), 1)
        self.assertEqual(
            commands[0],
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                "io.ha_pxe.managed=true",
                "--label",
                "io.ha_pxe.client_serial=serial123",
                MANAGED_DOCKER_NETWORK_NAME,
            ],
        )


if __name__ == "__main__":
    unittest.main()

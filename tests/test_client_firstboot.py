from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.client.firstboot import ensure_networkmanager_ready, ensure_networkmanager_resolver


class _FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(f"info:{message}")

    def warning(self, message: str) -> None:
        self.messages.append(f"warn:{message}")


class EnsureNetworkManagerResolverTests(unittest.TestCase):
    def test_ensure_networkmanager_resolver_repoints_to_networkmanager_runtime_file(self) -> None:
        logger = _FakeLogger()

        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            resolv_path = root_dir / "etc" / "resolv.conf"
            resolv_path.parent.mkdir(parents=True)
            resolv_path.write_text("nameserver 8.8.8.8\n", encoding="utf-8")

            managed_resolv = root_dir / "run" / "NetworkManager" / "resolv.conf"
            managed_resolv.parent.mkdir(parents=True)
            managed_resolv.write_text(
                "search home.nyanya.org\nnameserver 192.168.25.1\n",
                encoding="utf-8",
            )

            target = ensure_networkmanager_resolver(logger, root=root_dir, attempts=1, delay_seconds=0)

            self.assertEqual(target, "/run/NetworkManager/resolv.conf")
            self.assertTrue(resolv_path.is_symlink())
            self.assertEqual(resolv_path.readlink(), Path("/run/NetworkManager/resolv.conf"))

    def test_ensure_networkmanager_resolver_raises_when_runtime_file_is_missing(self) -> None:
        logger = _FakeLogger()

        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            resolv_path = root_dir / "etc" / "resolv.conf"
            resolv_path.parent.mkdir(parents=True)
            resolv_path.write_text("nameserver 8.8.8.8\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "did not create /run/NetworkManager/resolv.conf"):
                ensure_networkmanager_resolver(logger, root=root_dir, attempts=1, delay_seconds=0)

            self.assertFalse(resolv_path.is_symlink())
            self.assertEqual(resolv_path.read_text(encoding="utf-8"), "nameserver 8.8.8.8\n")


class EnsureNetworkManagerReadyTests(unittest.TestCase):
    def test_ensure_networkmanager_ready_checks_service_state_then_resolver(self) -> None:
        logger = _FakeLogger()
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            check = kwargs.get("check", True)
            del check
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("ha_pxe.client.firstboot.run", side_effect=fake_run),
            patch("ha_pxe.client.firstboot.ensure_networkmanager_resolver", return_value="/run/NetworkManager/resolv.conf") as resolver_mock,
        ):
            target = ensure_networkmanager_ready(logger)

        self.assertEqual(
            commands,
            [
                ["systemctl", "is-active", "--quiet", "NetworkManager.service"],
            ],
        )
        resolver_mock.assert_called_once()
        self.assertEqual(target, "/run/NetworkManager/resolv.conf")

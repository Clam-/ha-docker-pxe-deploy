from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext, AddonPaths
from ha_pxe.runtime import shutdown, start_client_log_transport, start_nfs_server, start_tftp_server


class _RunningProcess:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []
        self._running = True
        self.wait_calls = 0

    def poll(self) -> None:
        return None if self._running else 0

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        self._running = False
        return 0

    def kill(self) -> None:
        self._running = False


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
                [
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8099",
                    "--log-path",
                    "/client-log",
                    "--command-path",
                    "/client-command",
                    "--commands-dir",
                    str(context.paths.client_commands_dir),
                    "--log-level",
                    "info",
                ],
            )


class StartNfsServerTests(unittest.TestCase):
    def test_start_nfs_server_retries_after_resetting_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            mounted: set[str] = set()
            run_calls: list[list[str]] = []
            spawned_commands: list[list[str]] = []
            nfsd_start_attempts = 0

            def fake_run(
                command: list[str],
                *,
                check: bool = True,
                capture_output: bool = False,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
                input_text: str | None = None,
                stdout: object | int | None = None,
                stderr: object | int | None = None,
            ) -> CompletedProcess[str]:
                del check, capture_output, cwd, env, input_text, stdout, stderr
                nonlocal nfsd_start_attempts
                run_calls.append(command)

                if command[:2] == ["mountpoint", "-q"]:
                    return CompletedProcess(command, 0 if command[2] in mounted else 1, "", "")
                if command[:3] == ["mount", "-t", "rpc_pipefs"]:
                    mounted.add(command[4])
                    return CompletedProcess(command, 0, "", "")
                if command[:3] == ["mount", "-t", "nfsd"]:
                    mounted.add(command[4])
                    return CompletedProcess(command, 0, "", "")
                if command[:3] == ["rpcinfo", "-p", "127.0.0.1"]:
                    return CompletedProcess(command, 0, "ready", "")
                if command == ["rpc.nfsd", "0"]:
                    return CompletedProcess(command, 0, "", "")
                if command[:1] == ["rpc.nfsd"]:
                    nfsd_start_attempts += 1
                    if nfsd_start_attempts == 1:
                        return CompletedProcess(command, 1, "already running", "")
                    return CompletedProcess(command, 0, "", "")
                return CompletedProcess(command, 0, "", "")

            def fake_spawn(command: list[str]) -> _RunningProcess:
                spawned_commands.append(command)
                return _RunningProcess(command)

            with (
                patch("ha_pxe.runtime.ensure_directory"),
                patch("ha_pxe.runtime.run", side_effect=fake_run),
                patch("ha_pxe.runtime.spawn", side_effect=fake_spawn),
                patch("ha_pxe.runtime.shutil.copy2"),
                patch("ha_pxe.runtime.time.sleep"),
                patch("ha_pxe.runtime.command_exists", return_value=True),
            ):
                start_nfs_server(context, "192.0.2.10")

            self.assertEqual(nfsd_start_attempts, 2)
            self.assertIn(["rpc.nfsd", "0"], run_calls)
            self.assertEqual(run_calls.count(["exportfs", "-ra"]), 2)
            self.assertIn(
                [
                    "rpc.nfsd",
                    "--host",
                    "192.0.2.10",
                    "--port",
                    "2049",
                    "--tcp",
                    "--no-udp",
                    "--no-nfs-version",
                    "4",
                    "--no-nfs-version",
                    "4.1",
                    "--no-nfs-version",
                    "4.2",
                    "8",
                ],
                run_calls,
            )
            self.assertEqual(
                spawned_commands,
                [
                    ["rpcbind", "-f", "-w"],
                    [
                        "rpc.statd",
                        "-F",
                        "-L",
                        "-n",
                        "192.0.2.10",
                        "-p",
                        "32765",
                        "-o",
                        "32766",
                        "-T",
                        "32768",
                        "-U",
                        "32768",
                    ],
                    ["rpc.mountd", "-F", "--manage-gids", "--port", "32767"],
                ],
            )

    def test_start_nfs_server_enables_debug_flag_when_debug_logging_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            context.logger.level = "debug"
            run_calls: list[list[str]] = []
            spawned_commands: list[list[str]] = []

            def fake_run(
                command: list[str],
                *,
                check: bool = True,
                capture_output: bool = False,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
                input_text: str | None = None,
                stdout: object | int | None = None,
                stderr: object | int | None = None,
            ) -> CompletedProcess[str]:
                del check, capture_output, cwd, env, input_text, stdout, stderr
                run_calls.append(command)
                if command[:2] == ["mountpoint", "-q"]:
                    return CompletedProcess(command, 1, "", "")
                if command[:3] == ["rpcinfo", "-p", "127.0.0.1"]:
                    return CompletedProcess(command, 0, "ready", "")
                return CompletedProcess(command, 0, "", "")

            def fake_spawn(command: list[str]) -> _RunningProcess:
                spawned_commands.append(command)
                return _RunningProcess(command)

            with (
                patch("ha_pxe.runtime.ensure_directory"),
                patch("ha_pxe.runtime.run", side_effect=fake_run),
                patch("ha_pxe.runtime.spawn", side_effect=fake_spawn),
                patch("ha_pxe.runtime.shutil.copy2"),
                patch("ha_pxe.runtime.command_exists", return_value=True),
            ):
                start_nfs_server(context, "192.0.2.10")

            self.assertIn(
                [
                    "rpc.nfsd",
                    "--host",
                    "192.0.2.10",
                    "--port",
                    "2049",
                    "--tcp",
                    "--no-udp",
                    "--no-nfs-version",
                    "4",
                    "--no-nfs-version",
                    "4.1",
                    "--no-nfs-version",
                    "4.2",
                    "--debug",
                    "8",
                ],
                run_calls,
            )
            self.assertIn(
                ["rpc.mountd", "-F", "--manage-gids", "--port", "32767"],
                spawned_commands,
            )

    def test_start_nfs_server_retries_until_nfsd_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            run_calls: list[list[str]] = []
            mounted: set[str] = set()
            nfsd_start_attempts = 0

            def fake_run(
                command: list[str],
                *,
                check: bool = True,
                capture_output: bool = False,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
                input_text: str | None = None,
                stdout: object | int | None = None,
                stderr: object | int | None = None,
            ) -> CompletedProcess[str]:
                del check, capture_output, cwd, env, input_text, stdout, stderr
                nonlocal nfsd_start_attempts
                run_calls.append(command)
                if command[:2] == ["mountpoint", "-q"]:
                    return CompletedProcess(command, 0 if command[2] in mounted else 1, "", "")
                if command[:3] == ["mount", "-t", "rpc_pipefs"]:
                    mounted.add(command[4])
                    return CompletedProcess(command, 0, "", "")
                if command[:3] == ["mount", "-t", "nfsd"]:
                    mounted.add(command[4])
                    return CompletedProcess(command, 0, "", "")
                if command[:3] == ["rpcinfo", "-p", "127.0.0.1"]:
                    return CompletedProcess(command, 0, "ready", "")
                if command == ["rpc.nfsd", "0"]:
                    return CompletedProcess(command, 0, "", "")
                if command[:1] == ["rpc.nfsd"]:
                    nfsd_start_attempts += 1
                    if nfsd_start_attempts < 4:
                        return CompletedProcess(command, 1, "knfsd is currently down", "")
                    return CompletedProcess(command, 0, "", "")
                return CompletedProcess(command, 0, "", "")

            with (
                patch("ha_pxe.runtime.ensure_directory"),
                patch("ha_pxe.runtime.run", side_effect=fake_run),
                patch("ha_pxe.runtime.spawn", return_value=_RunningProcess()),
                patch("ha_pxe.runtime.shutil.copy2"),
                patch("ha_pxe.runtime.command_exists", return_value=True),
                patch("ha_pxe.runtime.time.sleep"),
            ):
                start_nfs_server(context, "192.0.2.10")

            self.assertEqual(nfsd_start_attempts, 4)
            self.assertEqual(run_calls.count(["rpc.nfsd", "0"]), 4)
            self.assertEqual(run_calls.count(["exportfs", "-ra"]), 4)

    def test_start_nfs_server_fails_when_statd_exits_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))

            class _ExitedProcess:
                def poll(self) -> int:
                    return 1

            def fake_run(
                command: list[str],
                *,
                check: bool = True,
                capture_output: bool = False,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
                input_text: str | None = None,
                stdout: object | int | None = None,
                stderr: object | int | None = None,
            ) -> CompletedProcess[str]:
                del check, capture_output, cwd, env, input_text, stdout, stderr
                if command[:2] == ["mountpoint", "-q"]:
                    return CompletedProcess(command, 1, "", "")
                if command[:3] == ["rpcinfo", "-p", "127.0.0.1"]:
                    return CompletedProcess(command, 0, "ready", "")
                return CompletedProcess(command, 0, "", "")

            with (
                patch("ha_pxe.runtime.ensure_directory"),
                patch("ha_pxe.runtime.run", side_effect=fake_run),
                patch(
                    "ha_pxe.runtime.spawn",
                    side_effect=[_RunningProcess(["rpcbind", "-f", "-w"]), _ExitedProcess()],
                ),
                patch("ha_pxe.runtime.shutil.copy2"),
                patch("ha_pxe.runtime.command_exists", return_value=True),
            ):
                with self.assertRaisesRegex(Exception, "rpc.statd failed to start"):
                    start_nfs_server(context, "192.0.2.10")


class ShutdownTests(unittest.TestCase):
    def test_shutdown_stops_nfs_state_and_waits_for_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            context = AddonContext(paths=AddonPaths(root=temp_dir))
            process_one = _RunningProcess(["rpcbind", "-f", "-w"])
            process_two = _RunningProcess(["rpc.mountd", "-F", "--manage-gids"])
            context.background_processes.extend([process_one, process_two])
            run_calls: list[list[str]] = []
            mounted = {"/proc/fs/nfsd", "/var/lib/nfs/rpc_pipefs"}

            def fake_run(
                command: list[str],
                *,
                check: bool = True,
                capture_output: bool = False,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
                input_text: str | None = None,
                stdout: object | int | None = None,
                stderr: object | int | None = None,
            ) -> CompletedProcess[str]:
                del check, capture_output, cwd, env, input_text, stdout, stderr
                run_calls.append(command)

                if command[:2] == ["mountpoint", "-q"]:
                    return CompletedProcess(command, 0 if command[2] in mounted else 1, "", "")
                if command[:1] == ["umount"]:
                    mounted.discard(command[1])
                    return CompletedProcess(command, 0, "", "")
                return CompletedProcess(command, 0, "", "")

            with (
                patch("ha_pxe.runtime.run", side_effect=fake_run),
                patch(
                    "ha_pxe.runtime.capture_optional",
                    return_value=f"{context.paths.tftp_dir}/serial\n{context.paths.tftp_dir}/short",
                ),
            ):
                shutdown(context)

            self.assertIn(["rpc.nfsd", "0"], run_calls)
            self.assertIn(["exportfs", "-au"], run_calls)
            self.assertIn(["exportfs", "-f"], run_calls)
            self.assertIn(["umount", "/proc/fs/nfsd"], run_calls)
            self.assertIn(["umount", "/var/lib/nfs/rpc_pipefs"], run_calls)
            self.assertIn(["umount", f"{context.paths.tftp_dir}/short"], run_calls)
            self.assertIn(["umount", f"{context.paths.tftp_dir}/serial"], run_calls)
            self.assertEqual(process_one.wait_calls, 1)
            self.assertEqual(process_two.wait_calls, 1)
            self.assertEqual(context.background_processes, [])


if __name__ == "__main__":
    unittest.main()

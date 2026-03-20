"""Runtime helpers for PXE, NFS, and TFTP orchestration."""

from __future__ import annotations

import json
import filecmp
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .addon_context import AddonContext
from .errors import CommandError, HaPxeError
from .fs_utils import atomic_write, clear_directory, ensure_directory
from .shell import capture, capture_optional, run, spawn

NFS_RPC_PIPEFS = Path("/var/lib/nfs/rpc_pipefs")
NFS_PROC_FS = Path("/proc/fs/nfsd")
NFS_THREAD_COUNT = "8"
PROCESS_SHUTDOWN_TIMEOUT_SECONDS = 5


def ensure_directories(context: AddonContext) -> None:
    for path in (
        context.paths.cache_dir,
        context.paths.exports_dir,
        context.paths.runtime_dir,
        context.paths.state_dir,
        context.paths.tftp_dir,
        context.paths.tmp_dir,
    ):
        ensure_directory(path)
    atomic_write(context.paths.exports_file, "")


def require_mount_support(context: AddonContext) -> None:
    probe_dir = Path(tempfile.mkdtemp(dir=context.paths.tmp_dir, prefix="mount-check."))
    try:
        completed = run(["mount", "-t", "tmpfs", "-o", "size=1m", "tmpfs", str(probe_dir)], check=False, capture_output=True)
        if completed.returncode == 0:
            run(["umount", str(probe_dir)], check=False)
            return
        err_text = (completed.stderr or "").replace("\n", " ").strip()
    finally:
        if probe_dir.exists():
            probe_dir.rmdir()

    suffix = f" ({err_text})" if err_text else ""
    context.logger.error(f"Mount operations are blocked inside the add-on{suffix}.")
    context.logger.error(
        "Disable Home Assistant Protection mode for this add-on and restart it. Mount privileges are required to unpack Raspberry Pi images and run NFS."
    )
    raise HaPxeError("Mount operations are blocked inside the add-on")


def reset_runtime_state(context: AddonContext) -> None:
    for mount_point in reversed(_tftp_mounts(context)):
        run(["umount", mount_point], check=False)
    clear_directory(context.paths.tftp_dir)
    atomic_write(context.paths.exports_file, "")


def resolve_server_ip(context: AddonContext) -> str:
    configured = str(context.config.get("server_ip", "") or "")
    if configured:
        return configured

    route_output = capture(["ip", "-4", "route", "get", "1.1.1.1"])
    tokens = route_output.split()
    if "src" in tokens:
        index = tokens.index("src")
        if index + 1 < len(tokens):
            return tokens[index + 1]

    hostname_output = capture(["hostname", "-I"])
    if hostname_output:
        return hostname_output.split()[0]

    raise HaPxeError("Unable to auto-detect the server IP; set server_ip explicitly")


def normalize_serial(value: str) -> str:
    serial = value.lower().removeprefix("0x")
    if not serial or any(char not in "0123456789abcdef" for char in serial):
        raise HaPxeError(f"Invalid serial: {value}")
    return serial


def validate_model(model: str) -> bool:
    return model in {"pi0", "pi1", "pi2", "pi3", "pi4", "pi5", "400", "500", "cm3", "cm4", "cm5", "zero2w"}


def warn_if_model_needs_manual_attention(context: AddonContext, model: str) -> None:
    if model in {"pi0", "pi1"}:
        context.logger.warning(
            f"Model {model} does not have a typical onboard PXE path; image selection will work, but network boot may not"
        )
    elif model == "zero2w":
        context.logger.warning(
            "Model zero2w does not support standard wired network boot; image selection will work, but PXE may not"
        )


def image_arch_for_model(model: str, override: str = "auto") -> str:
    if override and override != "auto":
        return override
    if model in {"pi0", "pi1", "pi2"}:
        return "armhf"
    return "arm64"


def append_exports(context: AddonContext, boot_dir: Path, root_dir: Path) -> None:
    with context.paths.exports_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{root_dir} *(rw,sync,no_subtree_check,no_root_squash,insecure)\n")
        handle.write(f"{boot_dir} *(rw,sync,no_subtree_check,no_root_squash,insecure)\n")


def bind_tftp_tree(context: AddonContext, boot_dir: Path, serial: str, short_serial: str) -> None:
    full_target = context.paths.tftp_dir / serial
    short_target = context.paths.tftp_dir / short_serial
    ensure_directory(full_target)
    ensure_directory(short_target)
    run(["mount", "--bind", str(boot_dir), str(full_target)])
    if short_serial != serial:
        run(["mount", "--bind", str(boot_dir), str(short_target)])


def publish_root_tftp_firmware(context: AddonContext, boot_dir: Path, serial: str, model: str) -> None:
    if model not in {"pi2", "pi3", "cm3"}:
        context.logger.debug(
            f"Skipping shared root TFTP firmware for {serial}; model {model} uses EEPROM boot and should fetch prefixed start*.elf files instead"
        )
        return

    for file_name in ("bootcode.bin", "bootsig.bin"):
        source_path = boot_dir / file_name
        target_path = context.paths.tftp_dir / file_name
        if not source_path.exists():
            if file_name == "bootsig.bin":
                context.logger.debug(
                    f"No {file_name} present in {boot_dir}; Raspberry Pi ROMs commonly probe for it and usually accept file-not-found"
                )
            continue
        if not target_path.exists() or not filecmp.cmp(source_path, target_path, shallow=False):
            shutil.copy2(source_path, target_path)
            os.chmod(target_path, 0o644)
            context.logger.info(f"Publishing shared TFTP {file_name} from {serial}")
        else:
            context.logger.debug(f"Shared TFTP {file_name} already matches client {serial}")


def write_client_state(state_file: Path, model: str, arch: str, image_url: str) -> None:
    atomic_write(state_file, json.dumps({"model": model, "arch": arch, "image_url": image_url}, indent=2) + "\n")


def write_dhcp_hints(context: AddonContext, server_ip: str) -> None:
    atomic_write(
        context.paths.dhcp_hints_file,
        "\n".join(
            (
                f"Point Raspberry Pi PXE clients at {server_ip}",
                "",
                "Required DHCP concepts:",
                f"- next-server / option 66: {server_ip}",
                "- boot file / option 67: bootcode.bin or the matching Raspberry Pi firmware entrypoint for your board",
                "",
                "This add-on does not run DHCP or ProxyDHCP.",
                "",
            )
        ),
    )


def start_client_log_transport(context: AddonContext) -> None:
    port = str(context.paths.client_log_port)
    script_path = context.paths.library_dir / "client-log-server.py"
    context.logger.debug(f"Starting client log transport listener on TCP {port}{context.paths.client_log_path}")
    process = spawn(
        [
            str(script_path),
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--path",
            context.paths.client_log_path,
        ]
    )
    time.sleep(1)
    if process.poll() is not None:
        raise HaPxeError(f"Client log transport failed to start on TCP {port}{context.paths.client_log_path}")
    context.background_processes.append(process)
    context.logger.info(f"Client log transport is active on TCP {port}{context.paths.client_log_path}")


def start_nfs_server(context: AddonContext) -> None:
    ensure_directory(NFS_RPC_PIPEFS)
    ensure_directory(NFS_PROC_FS)
    if run(["mountpoint", "-q", str(NFS_RPC_PIPEFS)], check=False).returncode != 0:
        run(["mount", "-t", "rpc_pipefs", "sunrpc", str(NFS_RPC_PIPEFS)])
    if run(["mountpoint", "-q", str(NFS_PROC_FS)], check=False).returncode != 0:
        run(["mount", "-t", "nfsd", "nfsd", str(NFS_PROC_FS)])

    _reset_nfs_server_state(context)
    shutil.copy2(context.paths.exports_file, Path("/etc/exports"))

    rpcbind = spawn(["rpcbind", "-f", "-w"])
    context.background_processes.append(rpcbind)
    time.sleep(1)
    run(["exportfs", "-ra"])
    mountd = spawn(["rpc.mountd", "-F", "--manage-gids"])
    context.background_processes.append(mountd)
    _start_nfs_threads(context)
    context.logger.info("NFS exports are active")


def start_tftp_server(context: AddonContext, server_ip: str) -> None:
    command = [
        "dnsmasq",
        "--keep-in-foreground",
        "--port=0",
        "--enable-tftp",
        f"--listen-address={server_ip}",
        f"--tftp-root={context.paths.tftp_dir}",
        "--tftp-no-fail",
        "--log-facility=-",
        "--quiet-dhcp",
        "--quiet-dhcp6",
        "--quiet-ra",
        "--bind-interfaces",
    ]
    if context.logger.level == "debug":
        command.append("--log-debug")
        context.logger.info("TFTP request logging is enabled via dnsmasq")
    else:
        command.append("--quiet-tftp")
    context.logger.debug(f"Starting dnsmasq TFTP server with root {context.paths.tftp_dir}")
    process = spawn(command)
    context.background_processes.append(process)
    context.logger.info("TFTP server is active on UDP 69")


def shutdown(context: AddonContext) -> None:
    _terminate_background_processes(context)
    _reset_nfs_server_state(context, unmount=True)
    for mount_point in reversed(_tftp_mounts(context)):
        run(["umount", mount_point], check=False)


def _tftp_mounts(context: AddonContext) -> list[str]:
    output = capture_optional(["findmnt", "-rn", "-o", "TARGET"])
    root = f"{context.paths.tftp_dir}/"
    return [line for line in output.splitlines() if line.startswith(root)]


def _start_nfs_threads(context: AddonContext) -> None:
    completed = run(["rpc.nfsd", NFS_THREAD_COUNT], check=False, capture_output=True)
    if completed.returncode == 0:
        return

    detail = _command_output(completed)
    if detail:
        context.logger.warning(
            f"rpc.nfsd failed on the first attempt; resetting NFS state and retrying once ({detail})"
        )
    else:
        context.logger.warning("rpc.nfsd failed on the first attempt; resetting NFS state and retrying once")

    _reset_nfs_server_state(context)
    run(["exportfs", "-ra"])
    completed = run(["rpc.nfsd", NFS_THREAD_COUNT], check=False, capture_output=True)
    if completed.returncode != 0:
        raise CommandError(["rpc.nfsd", NFS_THREAD_COUNT], completed.returncode, completed.stderr or completed.stdout or "")


def _reset_nfs_server_state(context: AddonContext, *, unmount: bool = False) -> None:
    if _mountpoint_active(NFS_PROC_FS):
        run(["rpc.nfsd", "0"], check=False, capture_output=True)

    run(["exportfs", "-au"], check=False, capture_output=True)
    run(["exportfs", "-f"], check=False, capture_output=True)

    if not unmount:
        return

    for mount_point in (NFS_PROC_FS, NFS_RPC_PIPEFS):
        if _mountpoint_active(mount_point):
            run(["umount", str(mount_point)], check=False)


def _mountpoint_active(path: Path) -> bool:
    return run(["mountpoint", "-q", str(path)], check=False).returncode == 0


def _command_output(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stderr or completed.stdout or "").replace("\n", " ").strip()


def _terminate_background_processes(context: AddonContext) -> None:
    for process in reversed(context.background_processes):
        if process.poll() is None:
            process.terminate()

    for process in reversed(context.background_processes):
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=PROCESS_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    context.background_processes.clear()

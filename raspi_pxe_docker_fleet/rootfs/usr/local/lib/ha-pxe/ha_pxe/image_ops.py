"""Image download and extraction helpers for the add-on."""

from __future__ import annotations

import lzma
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .addon_context import AddonContext
from .errors import HaPxeError
from .fs_utils import clear_directory
from .shell import capture, capture_optional, run


def latest_image_url(context: AddonContext, arch: str) -> str:
    with urllib.request.urlopen(context.paths.os_page, timeout=30) as response:
        page = response.read().decode("utf-8", errors="replace")

    if arch == "armhf":
        pattern = r"https://downloads\.raspberrypi\.com/raspios_lite_armhf/images/[^\"\s]+\.img\.xz"
    else:
        pattern = r"https://downloads\.raspberrypi\.com/raspios_lite_arm64/images/[^\"\s]+\.img\.xz"

    match = re.search(pattern, page)
    if not match:
        raise HaPxeError(f"Unable to discover the latest Raspberry Pi OS Lite {arch} image URL")
    return match.group(0)


def download_image(context: AddonContext, url: str) -> Path:
    archive_path = context.paths.cache_dir / Path(url).name
    image_path = archive_path.with_suffix("")
    temp_archive = archive_path.with_suffix(f"{archive_path.suffix}.download")

    if not archive_path.exists() or archive_path.stat().st_size == 0:
        _download_with_progress(context, url, temp_archive, archive_path.name)
        temp_archive.replace(archive_path)
    else:
        context.logger.info(f"Reusing cached image archive {archive_path.name}")

    if not image_path.exists() or archive_path.stat().st_mtime > image_path.stat().st_mtime:
        context.logger.info(f"Decompressing {archive_path.name}")
        temp_image = image_path.with_suffix(f"{image_path.suffix}.tmp")
        with lzma.open(archive_path, "rb") as source, temp_image.open("wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        temp_image.replace(image_path)
    return image_path


def populate_from_image(context: AddonContext, image_path: Path, boot_dir: Path, root_dir: Path) -> None:
    mount_boot = Path(tempfile.mkdtemp(dir=context.paths.tmp_dir, prefix="boot."))
    mount_root = Path(tempfile.mkdtemp(dir=context.paths.tmp_dir, prefix="root."))
    loop_device = ""
    mounted_boot = False
    mounted_root = False

    try:
        context.logger.debug(f"Preparing loop device for {image_path.name}")
        _cleanup_loop_devices_for_image(context, image_path)
        loop_device = capture(["losetup", "--find", "--show", "--read-only", "--partscan", str(image_path)])
        if not loop_device:
            raise HaPxeError(f"Failed to create a loop device for {image_path}")

        boot_partition = f"{loop_device}p1"
        root_partition = f"{loop_device}p2"
        context.logger.debug(f"Attached {image_path.name} to {loop_device}")
        context.logger.debug(f"Waiting for partition devices {boot_partition} and {root_partition}")
        if not _wait_for_block_device(boot_partition) or not _wait_for_block_device(root_partition):
            raise HaPxeError(f"Partition devices for {loop_device} did not appear")

        context.logger.debug(f"Mounting boot partition {boot_partition} to {mount_boot}")
        run(["mount", "-o", "ro", "-t", "vfat", boot_partition, str(mount_boot)])
        mounted_boot = True
        context.logger.debug(f"Mounting root partition {root_partition} to {mount_root}")
        run(["mount", "-o", "ro", "-t", "ext4", root_partition, str(mount_root)])
        mounted_root = True

        context.logger.debug(f"Clearing target boot export {boot_dir}")
        clear_directory(boot_dir)
        context.logger.debug(f"Clearing target root export {root_dir}")
        clear_directory(root_dir)

        context.logger.debug(f"Syncing boot files into {boot_dir}")
        run(["rsync", "-a", "--delete", f"{mount_boot}/", f"{boot_dir}/"])
        context.logger.debug(f"Syncing root filesystem into {root_dir}")
        run(["rsync", "-aHAX", "--numeric-ids", "--delete", f"{mount_root}/", f"{root_dir}/"])
        run(["sync"])
    finally:
        if mounted_root:
            run(["umount", str(mount_root)], check=False)
        if mounted_boot:
            run(["umount", str(mount_boot)], check=False)
        if loop_device:
            run(["losetup", "-d", loop_device], check=False)
        if mount_boot.exists():
            mount_boot.rmdir()
        if mount_root.exists():
            mount_root.rmdir()


def _download_with_progress(context: AddonContext, url: str, destination: Path, label: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ha-pxe"})
    content_length = _remote_content_length(url)
    last_percent = -5
    last_bytes = 0

    context.logger.info(f"Downloading {label}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                current_bytes = handle.tell()
                current_mib = _format_mib(current_bytes)
                if content_length and content_length > 0:
                    percent = current_bytes * 100 // content_length
                    if percent >= last_percent + 5 and percent < 100:
                        total_mib = _format_mib(content_length)
                        context.logger.info(f"Downloading {label}: {percent}% ({current_mib} MiB/{total_mib} MiB)")
                        last_percent = percent
                elif current_bytes >= last_bytes + 25 * 1024 * 1024:
                    context.logger.info(f"Downloading {label}: {current_mib} MiB received")
                    last_bytes = current_bytes
    except urllib.error.URLError as exc:
        destination.unlink(missing_ok=True)
        raise HaPxeError(f"Download failed for {label}: {exc.reason}") from exc

    current_bytes = destination.stat().st_size
    current_mib = _format_mib(current_bytes)
    if content_length and content_length > 0:
        total_mib = _format_mib(content_length)
        context.logger.info(f"Downloaded {label}: 100% ({current_mib} MiB/{total_mib} MiB)")
    else:
        context.logger.info(f"Downloaded {label}: {current_mib} MiB")


def _remote_content_length(url: str) -> int | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ha-pxe"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.headers.get("Content-Length")
    except urllib.error.URLError:
        return None
    if raw and raw.isdigit():
        return int(raw)
    return None


def _format_mib(value: int) -> int:
    return (value + 1_048_575) // 1_048_576


def _cleanup_loop_devices_for_image(context: AddonContext, image_path: Path) -> None:
    output = capture_optional(["losetup", "-j", str(image_path)])
    if not output:
        return
    mounted_sources = set(capture_optional(["findmnt", "-rn", "-o", "SOURCE"]).splitlines())
    for line in output.splitlines():
        loop_device = line.split(":", 1)[0].strip()
        if not loop_device:
            continue
        if loop_device in mounted_sources or any(source.startswith(f"{loop_device}p") for source in mounted_sources):
            context.logger.warning(f"Loop device {loop_device} is still mounted; leaving it attached")
            continue
        context.logger.warning(f"Detaching stale loop device {loop_device} for {image_path.name}")
        run(["losetup", "-d", loop_device], check=False)


def _wait_for_block_device(path: str, attempts: int = 20) -> bool:
    block_path = Path(path)
    for _ in range(attempts):
        if block_path.exists():
            return True
        time.sleep(0.5)
    return False

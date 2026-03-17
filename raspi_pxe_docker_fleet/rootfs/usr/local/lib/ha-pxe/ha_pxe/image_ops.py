"""Image download and extraction helpers for the add-on."""

from __future__ import annotations

import lzma
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .addon_context import AddonContext
from .errors import HaPxeError
from .fs_utils import clear_directory
from .shell import capture, capture_optional, run


HTTP_HEADERS = {
    "User-Agent": "curl/8.0.1",
    "Accept": "*/*",
}

SECTOR_SIZE_BYTES = 512


@dataclass(frozen=True)
class PartitionLoopDevice:
    number: int
    device: str


def latest_image_url(context: AddonContext, arch: str) -> str:
    request = _http_request(context.paths.os_page)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            page = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise HaPxeError(f"Unable to fetch the Raspberry Pi OS page: {reason}") from exc

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
    partition_loop_devices: list[PartitionLoopDevice] = []
    mounted_boot = False
    mounted_root = False

    try:
        context.logger.debug(f"Preparing loop device for {image_path.name}")
        _cleanup_loop_devices_for_image(context, image_path)
        partition_loop_devices = _attach_partition_loop_devices(context, image_path)

        boot_partition = _partition_device(partition_loop_devices, 1)
        root_partition = _partition_device(partition_loop_devices, 2)
        context.logger.debug(f"Attached boot partition to {boot_partition}")
        context.logger.debug(f"Attached root partition to {root_partition}")

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
        for partition_loop_device in reversed(partition_loop_devices):
            run(["losetup", "-d", partition_loop_device.device], check=False)
        if mount_boot.exists():
            mount_boot.rmdir()
        if mount_root.exists():
            mount_root.rmdir()


def _download_with_progress(context: AddonContext, url: str, destination: Path, label: str) -> None:
    request = _http_request(url)
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
    request = _http_request(url, method="HEAD")
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


def _http_request(url: str, *, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers=HTTP_HEADERS)


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


def _attach_partition_loop_devices(context: AddonContext, image_path: Path) -> list[PartitionLoopDevice]:
    partition_offsets = _read_partition_offsets(image_path)
    partition_loop_devices: list[PartitionLoopDevice] = []

    for number in (1, 2):
        offset_bytes, size_bytes = partition_offsets.get(number, (0, 0))
        if offset_bytes <= 0 or size_bytes <= 0:
            raise HaPxeError(f"Image {image_path.name} is missing partition {number}")

        loop_device = capture(
            [
                "losetup",
                "--find",
                "--show",
                "--read-only",
                "--offset",
                str(offset_bytes),
                "--sizelimit",
                str(size_bytes),
                str(image_path),
            ]
        )
        if not loop_device:
            raise HaPxeError(f"Failed to attach partition {number} from {image_path.name}")
        if not _wait_for_block_device(loop_device):
            raise HaPxeError(f"Loop device {loop_device} for partition {number} did not appear")

        context.logger.debug(
            f"Attached partition {number} from {image_path.name} to {loop_device} "
            f"(offset={offset_bytes}, size={size_bytes})"
        )
        partition_loop_devices.append(PartitionLoopDevice(number=number, device=loop_device))

    return partition_loop_devices


def _read_partition_offsets(image_path: Path) -> dict[int, tuple[int, int]]:
    output = capture(["partx", "-g", "--raw", "-o", "NR,START,SECTORS", str(image_path)])
    partition_offsets: dict[int, tuple[int, int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue

        try:
            number = int(fields[0])
            start_sector = int(fields[1])
            sector_count = int(fields[2])
        except ValueError:
            continue

        partition_offsets[number] = (start_sector * SECTOR_SIZE_BYTES, sector_count * SECTOR_SIZE_BYTES)

    return partition_offsets


def _partition_device(partition_loop_devices: list[PartitionLoopDevice], number: int) -> str:
    for partition_loop_device in partition_loop_devices:
        if partition_loop_device.number == number:
            return partition_loop_device.device
    raise HaPxeError(f"Attached loop device for partition {number} was not found")


def _wait_for_block_device(path: str, attempts: int = 20) -> bool:
    block_path = Path(path)
    for _ in range(attempts):
        if block_path.exists():
            return True
        time.sleep(0.5)
    return False

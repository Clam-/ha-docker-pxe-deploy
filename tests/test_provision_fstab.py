from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.provision import _rewrite_fstab


class RewriteFstabTests(unittest.TestCase):
    def test_rewrite_fstab_replaces_boot_mount_with_nfs_addr_option(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            etc_dir = root_dir / "etc"
            etc_dir.mkdir(parents=True)
            fstab_path = etc_dir / "fstab"
            fstab_path.write_text(
                "proc /proc proc defaults 0 0\n"
                "PARTUUID=11111111-02 / ext4 defaults,noatime 0 1\n"
                "PARTUUID=11111111-01 /boot/firmware vfat defaults 0 2\n"
                "/swapfile none swap sw 0 0\n"
                "tmpfs /tmp tmpfs defaults,nosuid 0 0\n",
                encoding="utf-8",
            )

            _rewrite_fstab(root_dir, "192.0.2.10", Path("/data/exports/test-client/boot"))

            self.assertEqual(
                fstab_path.read_text(encoding="utf-8"),
                "proc /proc proc defaults 0 0\n"
                "tmpfs /tmp tmpfs defaults,nosuid 0 0\n"
                "192.0.2.10:/data/exports/test-client/boot /boot/firmware nfs defaults,vers=3,tcp,nolock,_netdev,addr=192.0.2.10 0 0\n",
            )

    def test_rewrite_fstab_preserves_existing_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            etc_dir = root_dir / "etc"
            etc_dir.mkdir(parents=True)
            fstab_path = etc_dir / "fstab"
            fstab_path.write_text("proc /proc proc defaults 0 0\n", encoding="utf-8")
            os.chmod(fstab_path, 0o644)

            _rewrite_fstab(root_dir, "192.0.2.10", Path("/data/exports/test-client/boot"))

            self.assertEqual(fstab_path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()

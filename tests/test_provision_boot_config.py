from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext, AddonPaths
from ha_pxe.provision import _rewrite_boot_config


class RewriteBootConfigTests(unittest.TestCase):
    def _context(self, temp_dir: Path, boot_config_lines: str = "") -> AddonContext:
        context = AddonContext(paths=AddonPaths(root=temp_dir))
        context._config_cache = {"boot_config_lines": boot_config_lines}
        return context

    def test_rewrite_boot_config_appends_managed_block_to_main_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            boot_dir = temp_dir / "boot"
            boot_dir.mkdir()
            config_path = boot_dir / "config.txt"
            config_path.write_text(
                "# Base config\n"
                "dtparam=audio=on\n\n"
                "[pi4]\n"
                "dtoverlay=vc4-kms-v3d\n",
                encoding="utf-8",
            )

            context = self._context(temp_dir, " dtparam=i2c_arm=on \n")
            _rewrite_boot_config(context, boot_dir, {"boot_config_lines": "dtparam=spi=on\ndtparam=i2c_arm=on\n"})

            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "# Base config\n"
                "dtparam=audio=on\n\n"
                "[pi4]\n"
                "dtoverlay=vc4-kms-v3d\n\n"
                "# HA-PXE managed config start\n"
                "[all]\n"
                "dtparam=i2c_arm=on\n"
                "dtparam=spi=on\n"
                "[all]\n"
                "# HA-PXE managed config end\n",
            )

    def test_rewrite_boot_config_removes_existing_managed_block_when_no_lines_remain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            boot_dir = temp_dir / "boot"
            boot_dir.mkdir()
            config_path = boot_dir / "config.txt"
            config_path.write_text(
                "# Base config\n"
                "dtparam=audio=on\n\n"
                "# HA-PXE managed config start\n"
                "[all]\n"
                "dtparam=i2c_arm=on\n"
                "[all]\n"
                "# HA-PXE managed config end\n\n"
                "camera_auto_detect=1\n",
                encoding="utf-8",
            )

            context = self._context(temp_dir)
            _rewrite_boot_config(context, boot_dir, {})

            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "# Base config\n"
                "dtparam=audio=on\n\n"
                "camera_auto_detect=1\n",
            )

    def test_rewrite_boot_config_creates_main_config_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            boot_dir = temp_dir / "boot"
            boot_dir.mkdir()

            context = self._context(temp_dir, "dtparam=i2c_arm=on\n")
            _rewrite_boot_config(context, boot_dir, {})

            self.assertEqual(
                (boot_dir / "config.txt").read_text(encoding="utf-8"),
                "# HA-PXE managed config start\n"
                "[all]\n"
                "dtparam=i2c_arm=on\n"
                "[all]\n"
                "# HA-PXE managed config end\n",
            )

    def test_rewrite_boot_config_preserves_existing_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            boot_dir = temp_dir / "boot"
            boot_dir.mkdir()
            config_path = boot_dir / "config.txt"
            config_path.write_text("dtparam=audio=on\n", encoding="utf-8")
            os.chmod(config_path, 0o644)

            context = self._context(temp_dir, "dtparam=i2c_arm=on\n")
            _rewrite_boot_config(context, boot_dir, {})

            self.assertEqual(config_path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()

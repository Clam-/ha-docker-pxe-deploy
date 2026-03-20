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
from ha_pxe.provision import _rewrite_modules_conf


class RewriteModulesConfTests(unittest.TestCase):
    def _context(self, temp_dir: Path) -> AddonContext:
        return AddonContext(paths=AddonPaths(root=temp_dir))

    def test_rewrite_modules_conf_adds_i2c_dev_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            modules_dir = root_dir / "etc" / "modules-load.d"
            modules_dir.mkdir(parents=True)
            modules_path = modules_dir / "modules.conf"
            modules_path.write_text("# Existing modules\nsnd_bcm2835\n", encoding="utf-8")

            _rewrite_modules_conf(self._context(temp_dir), root_dir, True)

            self.assertEqual(
                modules_path.read_text(encoding="utf-8"),
                "# Existing modules\nsnd_bcm2835\ni2c-dev\n",
            )

    def test_rewrite_modules_conf_removes_i2c_dev_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            modules_dir = root_dir / "etc" / "modules-load.d"
            modules_dir.mkdir(parents=True)
            modules_path = modules_dir / "modules.conf"
            modules_path.write_text("# Existing modules\ni2c-dev\nsnd_bcm2835\n", encoding="utf-8")

            _rewrite_modules_conf(self._context(temp_dir), root_dir, False)

            self.assertEqual(
                modules_path.read_text(encoding="utf-8"),
                "# Existing modules\nsnd_bcm2835\n",
            )

    def test_rewrite_modules_conf_preserves_existing_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            modules_dir = root_dir / "etc" / "modules-load.d"
            modules_dir.mkdir(parents=True)
            modules_path = modules_dir / "modules.conf"
            modules_path.write_text("snd_bcm2835\n", encoding="utf-8")
            os.chmod(modules_path, 0o644)

            _rewrite_modules_conf(self._context(temp_dir), root_dir, True)

            self.assertEqual(modules_path.stat().st_mode & 0o777, 0o644)

    def test_rewrite_modules_conf_adds_i2c_dev_when_vc_bus_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            root_dir = temp_dir / "root"
            modules_dir = root_dir / "etc" / "modules-load.d"
            modules_dir.mkdir(parents=True)
            modules_path = modules_dir / "modules.conf"
            modules_path.write_text("# Existing modules\nsnd_bcm2835\n", encoding="utf-8")

            _rewrite_modules_conf(self._context(temp_dir), root_dir, True)

            self.assertEqual(
                modules_path.read_text(encoding="utf-8"),
                "# Existing modules\nsnd_bcm2835\ni2c-dev\n",
            )


if __name__ == "__main__":
    unittest.main()

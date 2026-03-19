from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.provision import _disable_stock_firstboot_services


class DisableStockFirstbootServicesTests(unittest.TestCase):
    def test_masks_stock_firstboot_and_wait_online_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root_dir = Path(temp_dir_name)
            multi_user_wants = root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants"
            multi_user_wants.mkdir(parents=True)
            userconfig_wants = multi_user_wants / "userconfig.service"
            userconfig_wants.write_text("", encoding="utf-8")

            rename_config = root_dir / "etc" / "ssh" / "sshd_config.d" / "rename_user.conf"
            rename_config.parent.mkdir(parents=True)
            rename_config.write_text("Banner /usr/share/userconf-pi/sshd_banner\n", encoding="utf-8")

            banner_path = root_dir / "usr" / "share" / "userconf-pi" / "sshd_banner"
            banner_path.parent.mkdir(parents=True)
            banner_path.write_text("Stock banner\n", encoding="utf-8")

            _disable_stock_firstboot_services(root_dir)

            self.assertTrue((root_dir / "etc" / "systemd" / "system" / "userconfig.service").is_symlink())
            self.assertEqual((root_dir / "etc" / "systemd" / "system" / "userconfig.service").readlink(), Path("/dev/null"))
            self.assertTrue((root_dir / "etc" / "systemd" / "system" / "systemd-firstboot.service").is_symlink())
            self.assertEqual(
                (root_dir / "etc" / "systemd" / "system" / "systemd-firstboot.service").readlink(),
                Path("/dev/null"),
            )
            self.assertTrue(
                (root_dir / "etc" / "systemd" / "system" / "systemd-networkd-wait-online.service").is_symlink()
            )
            self.assertEqual(
                (root_dir / "etc" / "systemd" / "system" / "systemd-networkd-wait-online.service").readlink(),
                Path("/dev/null"),
            )
            self.assertFalse(userconfig_wants.exists())
            self.assertFalse(rename_config.exists())
            self.assertEqual(banner_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()

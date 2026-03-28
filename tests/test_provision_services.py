from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.provision import _disable_stock_firstboot_services, _prepare_networkmanager_rootfs


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


class PrepareNetworkManagerRootfsTests(unittest.TestCase):
    def test_prepare_networkmanager_rootfs_masks_conflicting_services_and_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root_dir = Path(temp_dir_name)
            resolv_path = root_dir / "etc" / "resolv.conf"
            resolv_path.parent.mkdir(parents=True)
            resolv_path.write_text("nameserver 8.8.8.8\n", encoding="utf-8")
            networkmanager_service = root_dir / "usr" / "lib" / "systemd" / "system" / "NetworkManager.service"
            networkmanager_service.parent.mkdir(parents=True)
            networkmanager_service.write_text("[Unit]\nDescription=NetworkManager\n", encoding="utf-8")

            multi_user_wants = root_dir / "etc" / "systemd" / "system" / "multi-user.target.wants"
            network_online_wants = root_dir / "etc" / "systemd" / "system" / "network-online.target.wants"
            multi_user_wants.mkdir(parents=True)
            network_online_wants.mkdir(parents=True)
            for service in ("dhcpcd.service", "networking.service", "systemd-networkd.service"):
                (multi_user_wants / service).write_text("", encoding="utf-8")
                (network_online_wants / service).write_text("", encoding="utf-8")

            _prepare_networkmanager_rootfs(root_dir)

            self.assertEqual(
                (root_dir / "etc" / "NetworkManager" / "conf.d" / "90-ha-pxe.conf").read_text(encoding="utf-8"),
                "# Managed by HA-PXE\n[main]\ndns=default\n\n[ifupdown]\nmanaged=true\n",
            )
            self.assertTrue(resolv_path.is_symlink())
            self.assertEqual(resolv_path.readlink(), Path("/run/NetworkManager/resolv.conf"))
            self.assertTrue((multi_user_wants / "NetworkManager.service").is_symlink())
            self.assertEqual(
                (multi_user_wants / "NetworkManager.service").readlink(),
                Path("/usr/lib/systemd/system/NetworkManager.service"),
            )
            for service in ("dhcpcd.service", "networking.service", "systemd-networkd.service"):
                service_path = root_dir / "etc" / "systemd" / "system" / service
                self.assertTrue(service_path.is_symlink())
                self.assertEqual(service_path.readlink(), Path("/dev/null"))
                self.assertFalse((multi_user_wants / service).exists())
                self.assertFalse((network_online_wants / service).exists())


if __name__ == "__main__":
    unittest.main()

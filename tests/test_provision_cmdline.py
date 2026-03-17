from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext, AddonPaths
from ha_pxe.provision import _rewrite_cmdline


class RewriteCmdlineTests(unittest.TestCase):
    def test_rewrite_cmdline_removes_local_root_boot_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            boot_dir = temp_dir / "boot"
            boot_dir.mkdir()
            (boot_dir / "cmdline.txt").write_text(
                "console=serial0,115200 root=PARTUUID=11111111-02 rootfstype=ext4 "
                "rootflags=subvol=@ rdinit=/lib/systemd/systemd "
                "systemd.run=/boot/firstrun.sh systemd.run_success_action=reboot "
                "systemd.unit=kernel-command-line.target rw rootwait splash splash\n",
                encoding="utf-8",
            )

            context = AddonContext(paths=AddonPaths(root=temp_dir))
            _rewrite_cmdline(context, boot_dir, "192.0.2.10", Path("/data/exports/test-client/root"))

            self.assertEqual(
                (boot_dir / "cmdline.txt").read_text(encoding="utf-8"),
                "console=serial0,115200 splash root=/dev/nfs rootfstype=nfs "
                "nfsroot=192.0.2.10:/data/exports/test-client/root,vers=3,tcp,nolock "
                "rw ip=dhcp rootwait\n",
            )


if __name__ == "__main__":
    unittest.main()

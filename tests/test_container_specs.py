from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.container_specs import normalize_container_specs
from ha_pxe.errors import SpecError


class NormalizeContainerSpecsTests(unittest.TestCase):
    def test_normalize_container_specs_keeps_explicit_container_name(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "rgpiod",
                    "container_name": "rgpiod",
                    "image": "docker.io/library/busybox:latest",
                }
            ]
        )

        specs = normalize_container_specs(raw)

        self.assertEqual(specs[0]["name"], "rgpiod")
        self.assertEqual(specs[0]["container_name"], "rgpiod")

    def test_normalize_container_specs_rejects_duplicate_explicit_container_names(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "rgpiod-a",
                    "container_name": "rgpiod",
                    "image": "docker.io/library/busybox:latest",
                },
                {
                    "name": "rgpiod-b",
                    "container_name": "rgpiod",
                    "image": "docker.io/library/alpine:latest",
                },
            ]
        )

        with self.assertRaisesRegex(SpecError, "explicit container_name values must be unique"):
            normalize_container_specs(raw)


if __name__ == "__main__":
    unittest.main()

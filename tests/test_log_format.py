from __future__ import annotations

import io
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


LIB_DIR = Path(__file__).resolve().parents[1] / "raspi_pxe_docker_fleet" / "rootfs" / "usr" / "local" / "lib" / "ha-pxe"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonLogger
from ha_pxe.log_format import format_log_line


class LogFormatTests(unittest.TestCase):
    def test_format_log_line_includes_timestamp_level_and_name(self) -> None:
        line = format_log_line(
            "warn",
            "hello",
            name="ha-pxe",
            color=False,
            now=datetime(2026, 3, 19, 8, 9, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(line, "[2026-03-19 08:09:10+0000] 🟡 [WARN] [ha-pxe] hello")


class AddonLoggerTests(unittest.TestCase):
    def test_info_emits_timestamped_log_line(self) -> None:
        fake_stderr = io.StringIO()
        logger = AddonLogger()

        with patch("ha_pxe.addon_context.sys.stderr", fake_stderr):
            logger.info("configured")

        self.assertRegex(
            fake_stderr.getvalue().strip(),
            re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}\] 🟢 \[INFO\] configured$"),
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.client_log_server import main


if __name__ == "__main__":
    raise SystemExit(main())


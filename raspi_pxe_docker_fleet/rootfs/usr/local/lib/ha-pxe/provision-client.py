#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from ha_pxe.addon_context import AddonContext
from ha_pxe.provision import provision_client


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: provision-client.py <client-json> <server-ip>", file=sys.stderr)
        return 2
    context = AddonContext()
    context.configure_logging()
    provision_client(context, json.loads(sys.argv[1]), sys.argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


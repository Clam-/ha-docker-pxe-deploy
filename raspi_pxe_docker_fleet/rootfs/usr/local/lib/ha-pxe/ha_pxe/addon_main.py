"""Main add-on entrypoint."""

from __future__ import annotations

import os
import signal
import time
from typing import Any

from .addon_context import AddonContext
from .container_specs import normalize_container_specs
from .errors import HaPxeError, SpecError
from .provision import provision_client
from .runtime import (
    ensure_directories,
    require_mount_support,
    reset_runtime_state,
    resolve_server_ip,
    shutdown,
    start_client_log_transport,
    start_nfs_server,
    start_tftp_server,
    write_dhcp_hints,
)


def main() -> int:
    context = AddonContext()
    exit_code = 0
    try:
        ensure_directories(context)
        context.configure_logging()
        reset_runtime_state(context)
        _validate_config(context)
        require_mount_support(context)

        server_ip = resolve_server_ip(context)
        os.environ["HA_PXE_SERVER_IP"] = server_ip

        start_client_log_transport(context)
        context.logger.info(f"Using {server_ip} as the PXE/NFS endpoint")
        write_dhcp_hints(context, server_ip)

        for client in context.config.get("clients", []):
            if not isinstance(client, dict):
                raise HaPxeError("Each client entry must be an object")
            provision_client(context, client, server_ip)

        start_nfs_server(context)
        start_tftp_server(context)
        context.logger.warning(
            f"DHCP or ProxyDHCP is not included. Your network must direct PXE clients to {server_ip} for TFTP."
        )
        _wait_for_background_processes(context)
    except (HaPxeError, SpecError) as exc:
        context.logger.error(str(exc))
        exit_code = 1
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        shutdown(context)
    return exit_code


def _validate_config(context: AddonContext) -> None:
    password = str(context.config.get("default_password", "") or "")
    keys = str(context.config.get("ssh_authorized_keys", "") or "")
    if not password and not keys:
        raise HaPxeError("Set either default_password or ssh_authorized_keys so the client user can be accessed")

    clients = context.config.get("clients", [])
    if clients and not isinstance(clients, list):
        raise HaPxeError("clients must be an array")

    seen_serials: set[str] = set()
    for client in clients:
        if not isinstance(client, dict):
            raise HaPxeError("Each client entry must be an object")
        serial = str(client.get("serial", "")).lower().removeprefix("0x")
        if serial in seen_serials:
            raise HaPxeError("Client serial numbers must be unique")
        seen_serials.add(serial)
        try:
            normalize_container_specs(str(client.get("containers", "") or ""), context.mqtt_env_defaults())
        except SpecError as exc:
            raise HaPxeError(f"Client {client.get('serial', '')} has an invalid containers configuration: {exc}") from exc

    context.logger.info(f"Default client user is {context.config.get('default_username', 'pi')}")


def _wait_for_background_processes(context: AddonContext) -> None:
    interrupted = False

    def handle_signal(signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        context.logger.warning(f"Received signal {signum}; shutting down")

    previous_int = signal.signal(signal.SIGINT, handle_signal)
    previous_term = signal.signal(signal.SIGTERM, handle_signal)
    try:
        while context.background_processes and not interrupted:
            for process in list(context.background_processes):
                return_code = process.poll()
                if return_code is None:
                    continue
                if return_code == 0:
                    raise HaPxeError("A background service exited unexpectedly")
                raise HaPxeError(f"A background service exited with status {return_code}")
            time.sleep(1)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


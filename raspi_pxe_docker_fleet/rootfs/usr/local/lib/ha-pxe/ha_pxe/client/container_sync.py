"""Client container reconciliation entrypoint."""

from __future__ import annotations

from ..errors import HaPxeError
from ..shell import command_exists
from .bootstrap import BootstrapConfig, ClientPaths
from .container_engine import (
    cleanup_stale_containers,
    cleanup_stale_state_dirs,
    describe_desired_keys,
    describe_managed_containers,
    describe_state_dirs,
    ensure_docker_running,
    ensure_managed_network,
    load_desired_specs,
    reconcile_container,
    spec_key,
)
from .firstboot import repair_kernel_dhcp_resolver_if_needed
from .logging import ClientLogger


def main() -> int:
    config = BootstrapConfig.load()
    paths = ClientPaths()
    logger = ClientLogger(config, prefix="ha-pxe-container-sync", source="container-sync")

    try:
        logger.stage_start("preflight", f"Starting managed container reconciliation for {config.hostname} ({config.serial})")
        if not command_exists("docker"):
            if command_exists("dockerd"):
                raise HaPxeError(
                    "Docker daemon is running but the Docker CLI is missing; install docker-cli before container reconciliation can proceed"
                )
            logger.stage_skip("preflight", "Docker CLI is not installed on the client yet; skipping container reconciliation")
            return 0
        logger.stage_complete("preflight", "Docker CLI is available")

        repair_kernel_dhcp_resolver_if_needed(logger)

        logger.stage_start("validate", "Validating the desired container specification file")
        specs = load_desired_specs(paths.containers_json)
        logger.stage_complete("validate", f"Loaded {len(specs)} desired container definition(s)")

        logger.stage_start("docker", "Ensuring the Docker daemon is available")
        ensure_docker_running(logger)
        paths.state_root.mkdir(parents=True, exist_ok=True)
        logger.stage_complete("docker", "Docker is ready and state directories exist")

        if any(not spec.get("network_mode") for spec in specs):
            logger.stage_start("network", "Ensuring the managed Docker bridge network is available")
            ensure_managed_network(logger, config.serial)
            logger.stage_complete("network", "Managed Docker bridge network is ready")

        desired_keys: dict[str, int] = {}
        desired_names: dict[str, str] = {}
        for spec in specs:
            key = spec_key(spec)
            desired_keys[key] = 1
            desired_names[key] = str(spec["name"])

        logger.stage_start("reconcile", "Reconciling each desired managed container")
        had_error = False
        for spec in specs:
            try:
                reconcile_container(spec, paths.state_root, logger, config.serial, config.default_timezone)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to reconcile {spec['name']}: {exc}")
                had_error = True
        if had_error:
            logger.stage_fail("reconcile", "One or more managed containers failed to reconcile")
            logger.stage_skip("cleanup", "Skipping stale managed resource cleanup because one or more containers failed to reconcile")
            return 1
        logger.stage_complete("reconcile", "All managed containers were reconciled successfully")

        logger.stage_start("cleanup", "Removing stale managed containers and cached state")
        logger.info(f"Cleanup desired containers: {describe_desired_keys(desired_keys, desired_names)}")
        logger.info(f"Managed container inventory before cleanup: {describe_managed_containers(config.serial)}")
        logger.info(f"Managed state directories before cleanup: {describe_state_dirs(paths.state_root)}")
        cleanup_stale_containers(desired_keys, logger, config.serial)
        cleanup_stale_state_dirs(paths.state_root, desired_keys, logger)
        logger.stage_complete("cleanup", "Stale managed containers and state directories were cleaned up")

        logger.stage_start("summary", "Finalizing container reconciliation")
        logger.stage_complete("summary", "Managed container reconciliation completed successfully")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.fail_exception(exc)
        return 1

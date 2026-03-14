# Home Assistant Raspberry Pi PXE Fleet

This repository is structured as a Home Assistant add-on repository.

It contains one add-on, `raspi_pxe_docker_fleet`, that provisions Raspberry Pi
OS Lite images for PXE-style network boot, exports a per-client NFS root, and
pushes a simple Docker image list down to each booted client.

The add-on uses the standard Home Assistant add-on base image and installs the
required services with `apk` inside the add-on container.

## Repository layout

- `repository.yaml`: Home Assistant add-on repository metadata.
- `raspi_pxe_docker_fleet/`: the add-on itself.

## What the add-on does

- Downloads the latest Raspberry Pi OS Lite image for each client architecture.
- Creates a per-client boot tree and NFS root export.
- Serves the boot files over TFTP.
- Serves the root filesystem over NFS.
- Injects a first-boot bootstrap service into the exported rootfs.
- Creates a named user on the client at first boot.
- Installs Docker on the client and reconciles a configured list of container
  images.

## Important constraints

- DHCP or ProxyDHCP is not bundled here. Your network must already advertise the
  add-on host as the Raspberry Pi TFTP/PXE server, or you must add that in your
  router or DHCP server.
- The add-on manages simple image-only workloads. Each configured image is run
  as `docker run -d --restart unless-stopped IMAGE`. Per-container port, volume,
  and environment configuration is not modeled yet.
- The current add-on implementation uses Alpine `nfs-utils` on the stock Home
  Assistant base image for NFS exports. If you specifically need
  NFS-Ganesha, that likely requires a different package source or a custom
  image.
- Raspberry Pi network boot support varies by board and bootloader EEPROM state.
  The add-on accepts multiple model families for image selection, but your board
  still needs to support and be configured for network boot.

## Install

1. Add this repository to Home Assistant as an add-on repository.
2. Install `Raspberry Pi PXE Docker Fleet`.
3. Configure at least one client entry, a username, and either a password or
   SSH authorized keys.
4. Point your DHCP infrastructure at the Home Assistant host for TFTP boot.

See [`raspi_pxe_docker_fleet/DOCS.md`](./raspi_pxe_docker_fleet/DOCS.md) for
the full add-on configuration and operational notes.

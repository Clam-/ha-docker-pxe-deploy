# Home Assistant Raspberry Pi PXE Fleet

This repository contains the Home Assistant add-on `raspi_pxe_docker_fleet`.

The add-on prepares Raspberry Pi network-boot clients by:

- serving boot files over TFTP
- serving a per-client root filesystem over NFS
- creating a user on first boot
- installing Docker on the client
- building or pulling configured Docker workloads on that client and reconciling them locally

## Install

1. Add this repository to Home Assistant as an add-on repository.
2. Install `Raspberry Pi PXE Docker Fleet`.
3. Disable Protection mode before starting the add-on.
4. Configure at least one Raspberry Pi client plus a login method.
5. Point your DHCP or ProxyDHCP service at the Home Assistant host for TFTP.

## Notes

- DHCP or ProxyDHCP is not included in this add-on.
- Raspberry Pi network boot still depends on the board model and bootloader
  state.
- Container management supports both simple shorthand entries and richer JSON
  specs for remote builds, generated config files, and Docker run options.

See [`raspi_pxe_docker_fleet/DOCS.md`](./raspi_pxe_docker_fleet/DOCS.md) for configuration examples and operational details.

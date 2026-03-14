# Raspberry Pi PXE Docker Fleet

This add-on prepares Raspberry Pi network-boot clients from Home Assistant.
Each configured client gets:

- a TFTP boot tree
- a dedicated NFS root filesystem
- a first-boot user setup
- Docker installed on first boot
- a simple list of Docker images to run

## Before you start

- Disable Home Assistant Protection mode before starting the add-on.
- Make sure your Raspberry Pi model and bootloader support network boot.
- Provide DHCP or ProxyDHCP separately. This add-on does not serve DHCP.

## Example configuration

```yaml
log_level: info
server_ip: 192.168.25.250
default_username: pi
default_password: ""
default_timezone: Australia/Melbourne
default_keyboard_layout: us
default_locale: en_AU.UTF-8
ssh_authorized_keys: |
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE6A4C2WQY0gVxk7bP5fA8Bf4m3jX9pW5rP8YqL3m7wN lee@example-macbook
  ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDJ1i4WqQzK8V7o0xJ3mQeP1Xr1d9Yxk5oG4nM7lR0pA2uN6sB8wQ3nK6vM9cT1yP4wF7aL2mD8hQ9nS3gB5fL1zK0pR6tN8vQ2xC4mH7jL9sP1dF3gH5jK7mN9pQ2rT4vW6xY8zA0bC2dE4fG6hJ8kL0mN2pQ4rS6tU8vW0xY2z user@example
clients:
  - serial: "cdc843d7"
    model: pi3
    hostname: janky
    image_arch: arm64
    rebuild: false
    containers: |
      ghcr.io/home-assistant/home-assistant:stable
      ghcr.io/linuxserver/watchtower:latest
  - serial: "0x4f2c1a7b"
    model: pi4
    hostname: kitchen-pi.home.example
    image_arch: auto
    rebuild: false
    containers: |
      docker.io/library/nginx:1.27-alpine
      ghcr.io/example/sensor-agent:latest
```

## Field reference

- `log_level`: `error`, `warn`, `info`, or `debug`.
  `debug` adds verbose provisioning logs and TFTP request logs.
- `server_ip`: Optional override for the IP address clients should use for TFTP
  and NFS. Leave it blank to auto-detect.
- `default_username`: User created on each Raspberry Pi at first boot.
- `default_password`: Optional password for that user.
- `default_timezone`: Optional IANA timezone name to apply on first boot, such
  as `Australia/Melbourne` or `America/New_York`. Leave blank to keep the image
  default. Full list: `https://data.iana.org/time-zones/tzdb-2025a/zone1970.tab`
- `default_keyboard_layout`: Optional XKB keyboard layout code to apply on
  first boot, such as `gb`, `us`, or `de`. Leave blank to keep the image
  default. Layout list: `https://sources.debian.org/src/xkeyboard-config/2.42-1/rules/base.lst/`
- `default_locale`: Optional locale name to apply on first boot, such as
  `en_AU.UTF-8`, `en_US.UTF-8`, or `de_DE.UTF-8`. Leave blank to keep the image
  default. Locale list: `https://sources.debian.org/src/glibc/2.31-11/localedata/SUPPORTED/`
- `ssh_authorized_keys`: Optional newline-separated OpenSSH public keys.
  Each key must be the full line, including key type, base64 payload, and
  optional comment.
- `clients`: List of Raspberry Pi clients to provision.

Client fields:

- `serial`: Raspberry Pi serial number. Hex strings with or without a `0x`
  prefix are accepted.
- `model`: One of `pi0`, `pi1`, `pi2`, `pi3`, `pi4`, `pi5`, `400`, `500`,
  `cm3`, `cm4`, `cm5`, or `zero2w`.
- `hostname`: Hostname written into the client root filesystem. Short hostnames
  and dotted names are accepted.
- `image_arch`: `auto`, `armhf`, or `arm64`. `auto` maps older boards to
  `armhf` and newer boards to `arm64`.
- `rebuild`: If `true`, the client boot and root exports are recreated from a
  fresh Raspberry Pi OS Lite image on the next start.
- `containers`: Newline-separated Docker image references. Each line should be
  a normal image string such as `ghcr.io/home-assistant/home-assistant:stable`
  or `docker.io/library/nginx:1.27-alpine`.

## Common examples

Example `ssh_authorized_keys` value:

```yaml
ssh_authorized_keys: |
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE6A4C2WQY0gVxk7bP5fA8Bf4m3jX9pW5rP8YqL3m7wN lee@example-macbook
```

Example `containers` value:

```yaml
containers: |
  ghcr.io/home-assistant/home-assistant:stable
  ghcr.io/linuxserver/watchtower:latest
  docker.io/library/busybox:1.36
```

Example minimal single-client configuration:

```yaml
log_level: info
server_ip: ""
default_username: pi
default_password: ""
default_timezone: ""
default_keyboard_layout: ""
default_locale: ""
ssh_authorized_keys: |
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE6A4C2WQY0gVxk7bP5fA8Bf4m3jX9pW5rP8YqL3m7wN lee@example-macbook
clients:
  - serial: "cdc843d7"
    model: pi3
    hostname: janky
    image_arch: arm64
```

## DHCP requirement

Your network must tell Raspberry Pi boot clients to use the Home Assistant host
for TFTP. At minimum you usually need:

- next-server / option 66: the add-on `server_ip`
- boot file / option 67: the Raspberry Pi firmware entrypoint for your board

If your main router cannot do this, use a separate DHCP or ProxyDHCP service on
the network.

## Operational notes

- `rebuild: true` replaces the exported boot and root trees for that client.
- Client container management is intentionally simple. Each configured image is
  pulled and started with Docker defaults.
- The client root filesystem is stored under `/data`, so client state survives
  add-on restarts.
- Raspberry Pi 2 v1.2, Pi 3, and CM3-class network boot first request
  `/bootcode.bin` from the TFTP root, then typically probe `/bootsig.bin`.
  The add-on only publishes root-level `bootcode.bin` for those legacy models.
- Raspberry Pi 4, 400, CM4, Pi 5, 500, and CM5 use the EEPROM bootloader
  instead of `bootcode.bin`. Those models should fetch `start4.elf` or
  `start.elf` from the per-client prefixed directory.

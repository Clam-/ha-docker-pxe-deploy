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

## Agent Guidance

This repository supports a remote-client Docker deployment model. If an agent or
automation uses the term `Docker.remote`, treat it as repository shorthand for:

- the Docker workload is built, configured, and run on the Raspberry Pi client
- the Home Assistant add-on only provisions the client and writes the desired
  deployment spec into that client root filesystem
- a Git URL is not a `docker pull` target; it is a remote build source that the
  client must clone or otherwise fetch locally before building

`Docker.remote` is not a literal add-on schema key. Agents should map it to one
of the supported `source` modes inside the client `containers` definition:

- `source.type: image`
  Use for normal registry images such as `docker.io/library/nginx:1.27-alpine`.
- `source.type: git`
  Use for repositories like `https://github.com/Clam-/ha-pxe-janky-thermostat.git`
  that should be cloned on the client and built there with `docker build`.
- `source.type: dockerfile_url`
  Use for self-contained raw Dockerfile URLs that can be fetched directly on the
  client and built locally.

When an agent sees a project that is “composable via Git URL” or “buildable from
a Git URL”, the correct interpretation in this repository is:

- do not model it as an image pull
- do not assume Docker Compose will be run directly by this add-on
- model it as a Git-backed remote build on the client
- translate Compose-like settings into the add-on container spec fields such as
  `env`, `files`, `volumes`, `ports`, `devices`, `network_mode`, `depends_on`,
  and `command`

Preferred agent behavior when generating configs:

- Use the JSON array form of `containers` for anything beyond a plain registry
  image.
- Set an explicit unique `name` for each container.
- Prefer `source.type: git` for GitHub repos with a normal `Dockerfile`.
- Use `files` to generate config blobs like `/config/config.json` on the client
  and bind-mount them into the container.
- Use `env` for runtime environment variables.
- Use `image` only as the output tag for locally built sources, unless the
  source is a real registry image pull.
- Treat Compose files as reference material to translate, not as the primary
  runtime mechanism for this add-on.

For the thermostat example, the correct agent output is a JSON `containers`
definition with:

- a separate `rgpiod` entry for `https://github.com/Clam-/docker-rgpio.git`
- `source.type: git`
- `depends_on: ["rgpiod"]` on the thermostat entry
- `url: https://github.com/Clam-/ha-pxe-janky-thermostat.git` for the thermostat
- `dockerfile: Dockerfile` for each Git-backed build
- generated `files` content for `/config/config.json`
- any required runtime `env` and `extra_hosts` overrides such as MQTT and pigpio endpoints

## Notes

- DHCP or ProxyDHCP is not included in this add-on.
- Raspberry Pi network boot still depends on the board model and bootloader
  state.
- Container management supports both simple shorthand entries and richer JSON
  specs for remote builds, generated config files, and Docker run options.

See [`raspi_pxe_docker_fleet/DOCS.md`](./raspi_pxe_docker_fleet/DOCS.md) for configuration examples and operational details.

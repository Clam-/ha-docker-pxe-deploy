# Raspberry Pi PXE Docker Fleet

This add-on provisions Raspberry Pi OS Lite network boot clients from Home
Assistant.

For each configured Raspberry Pi client, it will:

- Select the correct Raspberry Pi OS Lite image family.
- Download the latest Lite image from raspberrypi.com.
- Extract the boot and root partitions.
- Publish the boot files over TFTP.
- Export separate per-client boot and root directories over NFS.
- Inject a first-boot service that creates a user, enables SSH, installs
  Docker, and starts a recurring container reconciliation service.

## Add-on configuration

```yaml
server_ip: 192.168.1.10
default_username: pi
default_password: changeme
ssh_authorized_keys: |
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
clients:
  - serial: "0x12345678abcdef12"
    model: pi4
    hostname: kitchen-pi
    image_arch: auto
    rebuild: false
    containers: |
      ghcr.io/home-assistant/home-assistant:stable
      ghcr.io/linuxserver/watchtower:latest
  - serial: "abcdef12"
    model: pi2
    hostname: garage-pi
    image_arch: auto
    rebuild: false
    containers: |
      ghcr.io/example/sensor-agent:latest
```

## Option reference

- `server_ip`: Optional override for the Home Assistant host IP that the Pi
  clients should use for TFTP and NFS. Leave blank to auto-detect.
- `default_username`: The Linux account created on each Pi at first boot.
- `default_password`: Optional password for that user. Supply either a password
  or SSH keys.
- `ssh_authorized_keys`: Optional newline-separated SSH public keys for the
  created user.
- `clients`: List of Raspberry Pi clients to provision.

Client fields:

- `serial`: Raspberry Pi serial number used for the network boot directory.
- `model`: Model family used to select the image architecture.
- `hostname`: Hostname written into the client rootfs. Short names and dotted
  FQDN-style hostnames are accepted.
- `image_arch`: `auto`, `armhf`, or `arm64`. `auto` maps Pi 0/1/2 to `armhf`
  and newer families to `arm64`.
- `rebuild`: If `true`, the exported boot/root trees for that client are
  recreated from the latest image on the next add-on start.
- `containers`: Newline-separated Docker image references to pull and run on
  the client.

## DHCP requirement

This add-on does not run DHCP or ProxyDHCP. Your network still needs to tell
the Raspberry Pi bootloader to use the Home Assistant host as its TFTP server.

At minimum your DHCP environment must point Raspberry Pi PXE clients at:

- next-server / option 66: the add-on `server_ip`
- boot file / option 67: `bootcode.bin` for older boards, or the relevant
  Raspberry Pi firmware entrypoint for your platform

If your router cannot do this, place a small ProxyDHCP service on the network
or add the equivalent settings to a dnsmasq/ISC DHCP service outside this
add-on.

## Operational notes

- `rebuild: true` is destructive for that client export. It refreshes the boot
  and root trees from the latest Raspberry Pi OS Lite image.
- Docker workloads are deliberately simple in this first version. The client
  runtime only manages image references and starts them with Docker defaults.
- The add-on currently uses Alpine `nfs-utils` for the NFS service while
  staying on the standard Home Assistant add-on base image. If you need
  NFS-Ganesha specifically, that likely means introducing a different package
  source or moving to a custom image.
- The add-on exports both the boot tree and the root tree over NFS. The client
  boots with a kernel `root=/dev/nfs` argument and mounts the boot tree at
  `/boot/firmware` after the rootfs is up.
- Because the exported root is persistent under `/data`, client changes survive
  add-on restarts.

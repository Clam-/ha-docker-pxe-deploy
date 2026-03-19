ARG BUILD_FROM
FROM $BUILD_FROM

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apk add --no-cache \
    coreutils \
    curl \
    dnsmasq \
    dosfstools \
    e2fsprogs \
    findutils \
    iproute2 \
    jq \
    openssl \
    parted \
    python3 \
    rsync \
    shadow \
    util-linux \
    xz \
    nfs-utils

COPY rootfs /

RUN chmod a+x \
    /run.py \
    /usr/local/lib/ha-pxe/client-log-server.py \
    /usr/local/lib/ha-pxe/provision-client.py \
    /usr/local/lib/ha-pxe/templates/ha-pxe-early-log.py \
    /usr/local/lib/ha-pxe/templates/ha-pxe-firstboot.py \
    /usr/local/lib/ha-pxe/templates/ha-pxe-container-sync.py

CMD ["/run.py"]

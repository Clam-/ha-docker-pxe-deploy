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
    rsync \
    socat \
    shadow \
    util-linux \
    xz \
    nfs-utils

COPY rootfs /

RUN chmod a+x \
    /run.sh \
    /usr/local/lib/ha-pxe/client-log-server.sh \
    /usr/local/lib/ha-pxe/provision-client.sh \
    /usr/local/lib/ha-pxe/templates/ha-pxe-firstboot.sh \
    /usr/local/lib/ha-pxe/templates/ha-pxe-container-sync.sh

CMD ["/run.sh"]
